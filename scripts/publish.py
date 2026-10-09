#!/usr/bin/env python3
"""publish.py - apply Polly's decisions: build/merge the website entry, fetch + host PDFs,
verify links, push publications.json (the live page's JSON format), and kick off data-portal
ingestion.

Usage:
  publish.py                 apply every unapplied decision + sweep (uploaded PDFs, retries)
  publish.py --id ID         apply one decision (the dashboard calls this right after a click)
  publish.py --sweep         only look for newly uploaded PDFs / re-verify waiting items
  publish.py --no-push       do everything but git push

Entry format written (what squarespace-code-block.html renders):
  {id, doi, title, authors_html, venue, year, type: article|preprint, links:[{label,url}], pin, note?, news?}
  link labels: web, PDF, bioRxiv, bioRxiv PDF, OSF data, Zenodo, GitHub, figshare, ...
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from common import (CFG, DECISIONS, DONE, INGEST, PDFS, PENDING, PUBS, ROOT, STATUS, authors_html, data_label,
                    download, get_json, git_commit_push, head_ok, is_pdf, load_json, load_pubs, locked, log,
                    now_label, renumber, save_json, slug, strip_num, today)

SINGLETON = ("web", "pdf", "biorxiv", "biorxiv pdf", "arxiv", "arxiv pdf")


def label_for(url, default="data"):
    return data_label(url, default)


def hosted(path):
    return CFG["pages_base_url"].rstrip("/") + "/" + str(path).replace(str(ROOT) + "/", "")


def pdf_slug(item):
    return slug("%s%s-%s" % (item.get("first_author") or "paper", item.get("year") or "", item.get("title", "")[:40]), 70)


# --------------------------------------------------------------------------- PDFs
def publisher_pdf_urls(item):
    """Best-effort direct PDF URLs for the published version: Unpaywall, then publisher patterns."""
    doi = item.get("doi") or ""
    out = []
    if not doi:
        return out
    try:
        u = get_json("https://api.unpaywall.org/v2/%s?email=%s" % (doi, CFG["contact_email"]), retries=1)
        for loc in [u.get("best_oa_location")] + (u.get("oa_locations") or []):
            p = (loc or {}).get("url_for_pdf")
            if p and p not in out and "nihpp" not in p and "biorxiv" not in p:
                out.append(p)
    except Exception:
        pass
    suf = doi.split("/", 1)[1] if "/" in doi else ""
    if doi.startswith("10.1038/"):
        out.append("https://www.nature.com/articles/%s.pdf" % suf)
    if doi.startswith("10.1073/"):
        out.append("https://www.pnas.org/doi/pdf/%s" % doi)
    if doi.startswith("10.1126/"):
        out.append("https://www.science.org/doi/pdf/%s" % doi)
    if doi.startswith("10.1021/"):
        out.append("https://pubs.acs.org/doi/pdf/%s" % doi)
    if doi.startswith("10.15252/"):
        out.append("https://www.embopress.org/doi/pdf/%s" % doi)
    if doi.startswith("10.7554/"):
        out.append("https://elifesciences.org/articles/%s.pdf" % doi.split(".")[-1])
    if doi.startswith("10.1039/"):
        m = re.match(r"([a-z]\d)(lc|sc|an|cc|ra|nr|ob|cp)(\d+)", suf)
        if m:
            yr = {"c": 2013, "d": 2020}.get(m.group(1)[0], 2020) + int(m.group(1)[1])
            out.append("https://pubs.rsc.org/en/content/articlepdf/%d/%s/%s" % (yr, m.group(2), suf))
    if doi.startswith("10.1016/"):
        try:
            cr = get_json("https://api.crossref.org/works/%s?mailto=%s" % (doi, CFG["contact_email"]), retries=1)
            for pii in cr.get("message", {}).get("alternative-id", []):
                if re.match(r"^S\d{16}$", pii):
                    f = "%s-%s(%s)%s-%s" % (pii[:5], pii[5:9], pii[9:11], pii[11:16], pii[16])
                    out.append("https://www.cell.com/cell-systems/pdf/%s.pdf" % f)
        except Exception:
            pass
    return out


def try_download_pdf(urls, dest):
    notes = []
    for u in [x for x in urls if x]:
        try:
            p, ct = download(u, dest)
            if is_pdf(p):
                return p, "fetched from %s" % u
            os.remove(p)
            notes.append("%s -> not a PDF (%s)" % (u, ct[:40]))
        except Exception as e:
            notes.append("%s -> %s" % (u, str(e)[:80]))
    return None, "; ".join(notes)[:400]


def biorxiv_pdf_urls(doi):
    if not doi or not (doi.startswith("10.1101/") or doi.startswith("10.64898/")):
        return []
    return ["https://www.biorxiv.org/content/%sv%d.full.pdf" % (doi, v) for v in (3, 2, 1)]


def inbox_dirs():
    """Where PDFs can be dropped: dashboard uploads, the repo's pdfs/ folder (upload on GitHub),
    and the Drive folder (best-effort; macOS may block launchd processes from reading it)."""
    return [ROOT / "pdf-inbox", PDFS, Path(CFG["pdf_inbox"])]


def inbox_pdf(item, kind):
    """A PDF Polly supplied: exact expected name, or a filename containing the DOI suffix."""
    want = pdf_slug(item) + ("-preprint" if kind == "preprint" else "") + ".pdf"
    key = (item.get("doi") or "").split("/")[-1].lower()
    for inbox in inbox_dirs():
        try:
            files = [f for f in inbox.iterdir() if f.suffix.lower() == ".pdf"]
        except Exception:
            continue
        for f in files:
            if f.name.lower() == want.lower() and is_pdf(f):
                return f
        if inbox != PDFS and kind == "published":
            for f in files:
                if key and len(key) >= 6 and key in f.name.lower() and is_pdf(f):
                    return f
        if inbox == PDFS and kind == "published" and key and len(key) >= 6:
            for f in files:
                if key in f.name.lower() and is_pdf(f) and "preprint" not in f.name.lower():
                    return f
    return None


def fetch_pdfs(item):
    """Populate item['pdf_hosted'] / item['preprint_pdf_hosted']. Returns status notes."""
    notes = []
    PDFS.mkdir(exist_ok=True)
    base = pdf_slug(item)
    item["pdf_expected"] = base + ".pdf"
    item["preprint_pdf_expected"] = base + "-preprint.pdf"
    kind = item.get("type")
    # --- published / manual version
    if kind in ("article", "manual") and not item.get("pdf_hosted"):
        dest = PDFS / (base + ".pdf")
        src = inbox_pdf(item, "published")
        if src:
            if src.resolve() != dest.resolve():
                shutil.copy(src, dest)
            item["pdf_hosted"] = hosted(dest)
            item.pop("pdf_status", None)
            item.pop("pdf_publisher_url", None)
            notes.append("PDF taken from your upload (%s)" % src.name)
        elif kind == "article":
            cands = [item.get("pdf_url")] + item.get("pdf_candidates", []) + publisher_pdf_urls(item)
            p, note = try_download_pdf(cands, dest)
            if p:
                item["pdf_hosted"] = hosted(dest)
                item.pop("pdf_status", None)
                notes.append("published PDF " + note)
            else:
                for u in cands:
                    ok, code, n = head_ok(u)
                    if ok and code in (200, 403):
                        item["pdf_publisher_url"] = u
                        break
                item["pdf_status"] = ("the PDF button points at the publisher's PDF (may need a subscription); upload your copy to host it here"
                                      if item.get("pdf_publisher_url") else "waiting for the published PDF - upload it here")
                notes.append(item["pdf_status"])
        else:
            item["pdf_status"] = "waiting for the PDF - upload it here (expected name %s.pdf)" % base
            notes.append(item["pdf_status"])
    # --- preprint version
    pre = item.get("preprint") or item.get("preprint_self")
    if pre and not item.get("preprint_pdf_hosted"):
        existing = (pre.get("pdf") or "")
        if existing.startswith("/s/") or "fordycelab.com/s/" in existing or CFG["pages_base_url"] in existing:
            item["preprint_pdf_hosted"] = existing if existing.startswith("http") else "https://www.fordycelab.com" + existing
            notes.append("preprint PDF: using the copy already on the site")
            return notes
        dest = PDFS / (base + "-preprint.pdf")
        src = inbox_pdf(item, "preprint")
        if src:
            shutil.copy(src, dest)
            item["preprint_pdf_hosted"] = hosted(dest)
            notes.append("preprint PDF taken from your upload")
        else:
            cands = biorxiv_pdf_urls(pre.get("doi")) + [pre.get("pdf")] + ([item.get("pdf_url")] if kind == "preprint" else [])
            p, note = try_download_pdf(cands, dest)
            if p:
                item["preprint_pdf_hosted"] = hosted(dest)
                item.pop("preprint_pdf_status", None)
                notes.append("preprint PDF " + note)
            elif existing.startswith("http"):
                item["preprint_pdf_hosted"] = existing
                notes.append("preprint PDF: linking to %s" % existing)
            else:
                item["preprint_pdf_status"] = "could not fetch the preprint PDF - upload it here (expected name %s-preprint.pdf)" % base
                notes.append(item["preprint_pdf_status"])
    return notes


# --------------------------------------------------------------------------- entries
def make_links(item, dec, existing):
    links = []
    kind = item.get("type")
    if kind == "preprint":
        links.append(("web", item.get("web_url") or ("https://doi.org/" + item["doi"])))
        if item.get("preprint_pdf_hosted"):
            links.append(("PDF", item["preprint_pdf_hosted"]))
    elif kind == "manual":
        if item.get("pdf_hosted"):
            links.append(("PDF", item["pdf_hosted"]))
        if item.get("web_url"):
            links.append(("web", item["web_url"]))
    else:
        links.append(("web", item.get("web_url") or ("https://doi.org/" + item["doi"])))
        if item.get("pdf_hosted") or item.get("pdf_publisher_url"):
            links.append(("PDF", item.get("pdf_hosted") or item["pdf_publisher_url"]))
        pre = item.get("preprint") or {}
        if pre.get("web") or pre.get("doi"):
            arx = "arxiv" in (pre.get("venue") or "").lower()
            links.append(("arXiv" if arx else "bioRxiv", pre.get("web") or "https://doi.org/" + pre["doi"]))
            if item.get("preprint_pdf_hosted"):
                links.append(("arXiv PDF" if arx else "bioRxiv PDF", item["preprint_pdf_hosted"]))
    # carry over what the existing entry already had
    if existing:
        was_pre = existing.get("type") == "preprint" and kind == "article"
        for l in existing.get("links", []):
            lab, url = l.get("label", ""), l.get("url", "")
            if lab.lower() in ("web", "pdf"):
                if was_pre:
                    lab = "bioRxiv PDF" if lab.lower() == "pdf" else "bioRxiv"
                else:
                    continue                       # replaced above
            links.append((lab, url))
    repo_urls = [u.strip() for u in re.split(r"[\s,;]+", dec.get("repo_url") or "") if u.strip()]
    for u in repo_urls:
        links.append((label_for(u), u))
    for dl in item.get("data_links", []):
        links.append((dl.get("label") or label_for(dl["url"]), dl["url"]))
    # de-duplicate: by URL, and one button per singleton label; keep order
    out, seen_u, seen_l = [], set(), set()
    for lab, url in links:
        if not url:
            continue
        k = url.rstrip("/").lower()
        if k in seen_u or (lab.lower() in SINGLETON and lab.lower() in seen_l):
            continue
        seen_u.add(k)
        seen_l.add(lab.lower())
        out.append({"label": lab, "url": url})
    rank = {"web": 0, "pdf": 1, "biorxiv": 2, "arxiv": 2, "biorxiv pdf": 3, "arxiv pdf": 3}
    return sorted(out, key=lambda l: rank.get(l["label"].lower(), 4))


def make_entry(item, dec, existing):
    ah, notes = authors_html(item.get("authors", []))
    authors = strip_num(dec.get("html_override") or (existing or {}).get("authors_html") or ah)
    e = dict(existing or {})
    e.update({
        "id": (existing or {}).get("id") or ("https://doi.org/" + item["doi"] if item.get("doi") else "manual:" + slug(item.get("title", ""), 50)),
        "doi": item.get("doi") or (existing or {}).get("doi", ""),
        "title": item.get("title") or (existing or {}).get("title", ""),
        "authors_html": authors,
        "venue": item.get("venue") or (existing or {}).get("venue", ""),
        "year": item.get("year") or (existing or {}).get("year"),
        "type": "preprint" if item.get("type") == "preprint" else "article",
        "links": make_links(item, dec, existing),
        "pin": bool((existing or {}).get("pin")),
    })
    if notes and not e.get("note"):
        e["note"] = "; ".join(notes)
    if item.get("note"):
        e["note"] = item["note"]
    return e


def find_existing(pubs, item):
    want = {x for x in (item.get("merge_into"), item.get("doi"), item.get("website_entry_id"), item.get("id")) if x}
    for e in pubs["publications"]:
        if e.get("doi") in want or e.get("id") in want:
            return e
    return None


def upsert_entry(pubs, item, dec):
    existing = find_existing(pubs, item)
    new = make_entry(item, dec, existing)
    lst = [e for e in pubs["publications"] if e is not existing]
    idx = 0
    for i, e in enumerate(lst):
        if e.get("pin"):
            idx = i + 1
            continue
        if (e.get("year") or 0) <= (new["year"] or 0):
            idx = i
            break
        idx = i + 1
    lst.insert(idx, new)
    pubs["publications"] = lst
    pubs["count"] = len(lst)
    pubs["updated"] = today()
    return new


def verify_links(links):
    out = []
    base = CFG["pages_base_url"].rstrip("/") + "/"
    for l in links:
        label, url = l["label"], l["url"]
        if url.startswith(base) and (ROOT / url[len(base):]).exists():
            out.append({"label": label, "url": url, "ok": True, "code": 0, "note": ""})
            continue
        ok, code, note = head_ok(url)
        out.append({"label": label, "url": url, "ok": ok, "code": code, "note": note})
    return out


def apply_website(item, dec, pubs):
    notes = fetch_pdfs(item)
    if item.get("type") == "manual" and not item.get("pdf_hosted") and not item.get("web_url"):
        item["website_status"] = "waiting for the PDF before adding the entry"
        item["website_notes"] = notes
        return notes
    entry = upsert_entry(pubs, item, dec)
    checks = verify_links(entry["links"])
    bad = [c for c in checks if not c["ok"]]
    if bad:
        notes.append("link check FAILED for: " + ", ".join("%s (%s)" % (c["label"], c["note"]) for c in bad))
    warn = [c for c in checks if c["ok"] and c["note"]]
    if warn:
        notes.append("unverifiable (bot-blocked) links: " + ", ".join(c["label"] for c in warn))
    item["website_status"] = "published" if not bad else "published with link problems"
    item["website_entry_id"] = entry.get("doi") or entry["id"]
    item["website_links"] = checks
    item["website_notes"] = notes
    item["website_ts"] = now_label()
    return notes


def start_ingest(item, dec):
    INGEST.mkdir(exist_ok=True)
    log_path = INGEST / (slug(item["id"], 60) + ".log")
    subprocess.Popen([sys.executable, str(ROOT / "scripts" / "ingest_propose.py"), item["id"]],
                     stdout=open(log_path, "a"), stderr=subprocess.STDOUT, start_new_session=True)
    item["portal_status"] = "downloading the data repository and drafting a proposal (usually 5-20 min)"
    item["portal_started"] = now_label()


def sweep(items, pubs, decisions):
    """Attach PDFs that arrived (dashboard upload, repo pdfs/, Drive) and rebuild those entries."""
    changed = []
    for it in items:
        waiting = it.get("pdf_status") or it.get("preprint_pdf_status") or str(it.get("website_status", "")).startswith("waiting")
        if not waiting or decisions.get(it["id"], {}).get("website") is not True:
            continue
        before = (it.get("pdf_hosted"), it.get("preprint_pdf_hosted"))
        fetch_pdfs(it)
        if (it.get("pdf_hosted"), it.get("preprint_pdf_hosted")) != before:
            apply_website(it, decisions.get(it["id"], {}), pubs)
            changed.append(it["id"])
            log("sweep: PDF attached for %s" % it["id"])
    return changed


def finalize(items, done):
    keep = []
    for it in items:
        web_done = it.get("website_status") in ("published", "published with link problems", "skipped")
        portal_done = it.get("portal_status") in ("merged", "skipped", "rejected", None, "") and not it.get("portal_proposal")
        waiting_pdf = bool(it.get("pdf_status") or it.get("preprint_pdf_status"))
        if web_done and portal_done and not waiting_pdf:
            done[it["id"]] = {"title": it.get("title"), "website": it.get("website_status"), "portal": it.get("portal_status"),
                              "entry_id": it.get("website_entry_id"), "ts": now_label()}
        else:
            keep.append(it)
    return keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()
    if args.no_push:
        CFG["git_push"] = False

    summary = {"ts": now_label(), "applied": [], "swept": [], "errors": []}
    with locked(PENDING):
        items = load_json(PENDING, [])
        decisions = load_json(DECISIONS, {})
        done = load_json(DONE, {})
        pubs = load_pubs()
        by_id = {it["id"]: it for it in items}
        touched = False
        if not args.sweep:
            for pid, dec in decisions.items():
                if args.id and pid != args.id:
                    continue
                it = by_id.get(pid)
                if not it:
                    continue
                try:
                    if dec.get("website") is True and not dec.get("website_applied"):
                        notes = apply_website(it, dec, pubs)
                        dec["website_applied"] = now_label()
                        touched = True
                        summary["applied"].append({"id": pid, "title": it.get("title"), "notes": notes})
                        log("website: %s -> %s | %s" % (pid, it.get("website_status"), "; ".join(notes)))
                    elif dec.get("website") is False and it.get("website_status") != "skipped":
                        it["website_status"] = "skipped"
                        dec["website_applied"] = now_label()
                    if dec.get("portal") is True and not dec.get("portal_started") and dec.get("repo_url"):
                        start_ingest(it, dec)
                        dec["portal_started"] = now_label()
                        log("portal: ingestion started for %s (%s)" % (pid, dec["repo_url"]))
                    elif dec.get("portal") is True and not dec.get("repo_url"):
                        it["portal_status"] = "need a data repository URL to draft the portal proposal"
                    elif dec.get("portal") is False:
                        it["portal_status"] = "skipped"
                    it["status"] = "; ".join(x for x in [
                        "website: " + str(it.get("website_status", "-")),
                        ("portal: " + it["portal_status"]) if it.get("portal_status") else ""] if x)
                except Exception as e:
                    summary["errors"].append("%s: %s" % (pid, e))
                    it["status"] = "error: %s" % str(e)[:200]
                    log("ERROR applying %s: %s" % (pid, e))
        swept = sweep(items, pubs, decisions)
        if swept:
            touched = True
            summary["swept"] = swept
            for it in items:
                if it["id"] in swept:
                    it["status"] = "website: " + str(it.get("website_status", "-"))
        items = finalize(items, done)
        save_json(PENDING, items)
        save_json(DECISIONS, decisions)
        save_json(DONE, done)
        if touched:
            renumber(pubs)                 # oldest paper = 1, newest = total
            save_json(PUBS, pubs)
            try:
                committed, pushed, note = git_commit_push(ROOT, [PUBS, PDFS, PENDING, DONE, DECISIONS], "Publications update %s" % today())
                summary["git"] = note
                log("git: " + note)
            except Exception as e:
                summary["errors"].append("git: %s" % e)
                log("git ERROR: %s" % e)
    prev = load_json(STATUS, {})
    prev["last_publish"] = summary
    save_json(STATUS, prev)
    if summary["applied"] or summary["swept"] or summary["errors"]:
        try:
            subprocess.run([sys.executable, str(ROOT / "scripts" / "notify.py"), "--publish"], timeout=120)
        except Exception as e:
            log("notify failed: %s" % e)


if __name__ == "__main__":
    main()
