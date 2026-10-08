#!/usr/bin/env python3
"""poll.py - find NEW papers/preprints Polly authored and queue them for her decision.

Sources: OpenAlex (author ID; includes bioRxiv), PubMed E-utilities, arXiv, and Google
Scholar alert emails in Gmail (Scholar itself has no API). Results are merged by DOI, checked
against what is already on the website / already decided, paired with any preprint or
published version, and written to pending.json for the dashboard card.

Usage: poll.py [--dry-run] [--lookback DAYS]
"""
import argparse
import base64
import datetime
import html as htmlmod
import json
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET

from common import (CFG, DONE, PENDING, PUBS, STATE, authors_html, data_label, doi_from_url, esc, first_author_of,
                    fmt_author, get_json, http, is_pi, load_json, load_pubs, log, norm_doi, norm_title, save_json,
                    slug, title_sim, today, update_pending)

PREPRINT_SOURCES = ("biorxiv", "medrxiv", "arxiv", "ssrn", "research square", "chemrxiv", "preprints.org", "osf preprints")
DATA_SOURCES = ("zenodo", "osf", "open science framework", "figshare", "dryad", "github", "mendeley data")
SKIP_TYPES = ("dissertation", "dataset", "other", "paratext", "peer-review", "erratum", "editorial", "grant", "libguides", "supplementary-materials")
SKIP_VENUES = ("digital repository", "zenodo", "figshare", "dryad", "open science framework")


def is_polly(name):
    n = (name or "").replace(".", " ").lower()
    return "fordyce" in n and (n.strip().startswith("polly") or " polly" in n or re.search(r"\bp\s*m\b", n) or n.startswith("pm ") or n.startswith("p m ") or n.startswith("p fordyce"))


# --------------------------------------------------------------------------- OpenAlex
def is_preprint_work(w):
    if w.get("type") == "preprint":
        return True
    src = (((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "").lower()
    return any(k in src for k in PREPRINT_SOURCES)


def cand_from_openalex(w):
    doi = norm_doi(w.get("doi"))
    prim = w.get("primary_location") or {}
    src = ((prim.get("source") or {}).get("display_name") or "")
    pre = is_preprint_work(w)
    authors = [{"name": a.get("author", {}).get("display_name", ""),
                "corresponding": bool(a.get("is_corresponding")),
                "openalex_id": (a.get("author", {}).get("id") or "").rsplit("/", 1)[-1]}
               for a in w.get("authorships", [])]
    c = {
        "id": doi or (w.get("id") or "").rsplit("/", 1)[-1],
        "doi": doi,
        "openalex_id": (w.get("id") or "").rsplit("/", 1)[-1],
        "title": w.get("display_name") or "",
        "year": w.get("publication_year"),
        "date": w.get("publication_date") or "",
        "type": "preprint" if pre else "article",
        "venue": clean_venue(src),
        "authors": authors,
        "web_url": w.get("doi") or prim.get("landing_page_url") or "",
        "pdf_url": "",
        "preprint": None,
        "data_links": [],
        "sources": ["OpenAlex"],
        "verified": any(a["openalex_id"] == CFG["openalex_author_id"] for a in authors),
        "license": prim.get("license") or "",
    }
    oa = w.get("best_oa_location") or {}
    pdfs = []
    for loc in w.get("locations", []):
        s = ((loc.get("source") or {}).get("display_name") or "")
        sl = s.lower()
        if any(k in sl for k in PREPRINT_SOURCES) and not pre:
            pdoi = doi_from_url(loc.get("landing_page_url") or "") or doi_from_url(loc.get("pdf_url") or "")
            if c["preprint"] is None or (pdoi and not c["preprint"].get("doi")):
                c["preprint"] = {"doi": pdoi, "web": loc.get("landing_page_url") or "", "pdf": loc.get("pdf_url") or "",
                                 "venue": clean_venue(s)}
            continue
        for k in DATA_SOURCES:
            if k in sl:
                url = loc.get("landing_page_url") or ""
                if url and url not in [d["url"] for d in c["data_links"]]:
                    c["data_links"].append({"label": data_label(url), "url": url, "source": s})
        if loc.get("pdf_url") and not any(k in sl for k in PREPRINT_SOURCES):
            pdf = loc["pdf_url"]
            if "nihpp" in pdf:   # PMC copy of the *preprint*, not the published version
                continue
            pdfs.append(pdf)
    if oa.get("pdf_url") and "nihpp" not in oa["pdf_url"]:
        pdfs.insert(0, oa["pdf_url"])
    c["pdf_url"] = pdfs[0] if pdfs else ""
    c["pdf_candidates"] = pdfs
    # Zenodo concept DOI + version DOI are consecutive numbers: keep only the version record
    zen = [d for d in c["data_links"] if "zenodo" in d["url"]]
    if len(zen) == 2:
        ids = sorted(int(re.sub(r"\D", "", d["url"][-12:]) or 0) for d in zen)
        if ids[1] - ids[0] == 1:
            c["data_links"] = [d for d in c["data_links"] if "zenodo" not in d["url"] or str(ids[1]) in d["url"]]
    if pre and doi and ("biorxiv" in src.lower() or "medrxiv" in src.lower()):
        c["preprint_self"] = {"doi": doi, "web": c["web_url"], "pdf": oa.get("pdf_url") or prim.get("pdf_url") or ""}
    c["skip"] = (w.get("type") in SKIP_TYPES) or any(k in src.lower() for k in SKIP_VENUES)
    return c


def clean_venue(s):
    s = re.sub(r"\s*\(.*?\)\s*$", "", s or "")   # 'bioRxiv (Cold Spring Harbor Laboratory)' -> 'bioRxiv'
    return s.strip()


def openalex_recent(lookback):
    since = (datetime.date.today() - datetime.timedelta(days=lookback)).isoformat()
    out = []
    cursor = "*"
    while cursor:
        params = {"filter": "author.id:%s,from_publication_date:%s" % (CFG["openalex_author_id"], since),
                  "sort": "publication_date:desc", "per-page": "100", "cursor": cursor, "mailto": CFG["contact_email"]}
        d = get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params))
        out.extend(d.get("results", []))
        cursor = d.get("meta", {}).get("next_cursor")
        time.sleep(0.3)
    return out


def openalex_by_doi(doi):
    try:
        return get_json("https://api.openalex.org/works/https://doi.org/%s?mailto=%s" % (doi, CFG["contact_email"]), retries=2)
    except Exception:
        return None


# --------------------------------------------------------------------------- PubMed
def pubmed_recent(lookback):
    q = {"db": "pubmed", "term": "Fordyce PM[Author]", "reldate": str(lookback), "datetype": "edat",
         "retmax": "100", "retmode": "json", "tool": "fordycelab-pubs", "email": CFG["contact_email"]}
    d = get_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(q))
    ids = d.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    body, _, _ = http("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode(
        {"db": "pubmed", "id": ",".join(ids), "retmode": "xml", "tool": "fordycelab-pubs", "email": CFG["contact_email"]}))
    root = ET.fromstring(body)
    out = []
    for art in root.findall(".//PubmedArticle"):
        pmid = (art.findtext(".//PMID") or "").strip()
        doi = ""
        for aid in art.findall(".//ArticleIdList/ArticleId"):
            if aid.get("IdType") == "doi":
                doi = norm_doi(aid.text)
        title = "".join(art.find(".//ArticleTitle").itertext()) if art.find(".//ArticleTitle") is not None else ""
        journal = art.findtext(".//Journal/Title") or art.findtext(".//Journal/ISOAbbreviation") or ""
        year = art.findtext(".//JournalIssue/PubDate/Year") or art.findtext(".//ArticleDate/Year") or ""
        authors, verified, has_polly = [], False, False
        for a in art.findall(".//AuthorList/Author"):
            ln, ini = a.findtext("LastName") or "", a.findtext("Initials") or ""
            if not ln:
                continue
            name = "%s %s" % (" ".join(ini), ln) if ini else ln
            authors.append({"name": name, "corresponding": False})
            if ln.lower() == "fordyce" and ini.startswith("P"):
                affs = " ".join(x.text or "" for x in a.findall(".//AffiliationInfo/Affiliation"))
                fore = a.findtext("ForeName") or ""
                has_polly = ini in ("PM", "P") and ("polly" in fore.lower() or ini == "PM")
                if "stanford" in affs.lower() and has_polly:
                    verified = True
        if not has_polly:
            continue          # a different P. Fordyce (PubMed's author search is initials-prefix based)
        pre = "biorxiv" in journal.lower() or "medrxiv" in journal.lower()
        out.append({
            "id": doi or ("pmid:" + pmid), "doi": doi, "pmid": pmid, "title": title.rstrip("."),
            "year": int(year) if year.isdigit() else None, "date": "", "type": "preprint" if pre else "article",
            "venue": "bioRxiv" if pre else journal, "authors": authors,
            "web_url": ("https://doi.org/" + doi) if doi else ("https://pubmed.ncbi.nlm.nih.gov/%s/" % pmid),
            "pdf_url": "", "preprint": None, "data_links": [], "sources": ["PubMed"], "verified": verified,
        })
    return out


# --------------------------------------------------------------------------- arXiv
def arxiv_recent():
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": "au:Fordyce_P", "sortBy": "submittedDate", "sortOrder": "descending", "max_results": "25"})
    body = b""
    for i in range(3):
        try:
            body, _, _ = http(url, timeout=40, retries=1)
        except Exception as e:
            log("arXiv fetch failed (%s)" % e)
        if body.startswith(b"<?xml") or body.startswith(b"<feed"):
            break
        log("arXiv rate-limited; waiting")
        time.sleep(15 * (i + 1))
    if not (body.startswith(b"<?xml") or body.startswith(b"<feed")):
        log("arXiv: giving up this run (%r)" % body[:60])
        return []
    ns = {"a": "http://www.w3.org/2005/Atom", "ar": "http://arxiv.org/schemas/atom"}
    out = []
    for e in ET.fromstring(body).findall("a:entry", ns):
        names = [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)]
        if not any(re.search(r"\bPolly\b", n) or re.search(r"\bP\.? ?M\.? Fordyce\b", n) for n in names):
            continue   # a different P. Fordyce
        aid = e.findtext("a:id", "", ns)
        arx = aid.rsplit("/abs/", 1)[-1]
        doi = norm_doi(e.findtext("ar:doi", "", ns))
        pdf = ""
        for l in e.findall("a:link", ns):
            if l.get("title") == "pdf":
                pdf = l.get("href")
        out.append({
            "id": doi or ("arxiv:" + re.sub(r"v\d+$", "", arx)), "doi": doi, "arxiv_id": arx,
            "title": re.sub(r"\s+", " ", e.findtext("a:title", "", ns)).strip(),
            "year": int(e.findtext("a:published", "", ns)[:4] or 0) or None,
            "date": e.findtext("a:published", "", ns)[:10], "type": "preprint", "venue": "arXiv",
            "authors": [{"name": n, "corresponding": False} for n in names],
            "web_url": aid, "pdf_url": pdf, "preprint": None, "data_links": [], "sources": ["arXiv"], "verified": True,
        })
    return out


# --------------------------------------------------------------------------- Google Scholar alert emails
def scholar_alerts(lookback_days=21):
    try:
        from common import gmail_api
        q = "from:scholaralerts-noreply@google.com newer_than:%dd" % lookback_days
        msgs = gmail_api("messages", {"q": q, "maxResults": 40}).get("messages", [])
    except Exception as e:
        log("Scholar alerts: Gmail unavailable (%s)" % e)
        return []
    found = []
    for m in msgs:
        try:
            full = gmail_api("messages/" + m["id"], {"format": "full"})
        except Exception:
            continue
        html = ""

        def walk(part):
            nonlocal html
            if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
                html += base64.urlsafe_b64decode(part["body"]["data"] + "==").decode("utf-8", "replace")
            for p in part.get("parts", []) or []:
                walk(p)
        walk(full.get("payload", {}))
        for mm in re.finditer(r'<a\s+href="([^"]+)"[^>]*class="gse_alrt_title"[^>]*>(.*?)</a>\s*</h3>\s*<div[^>]*>(.*?)</div>', html, re.S):
            href, title, rest = mm.groups()
            href = htmlmod.unescape(href)
            title = htmlmod.unescape(re.sub(r"<[^>]+>", "", title)).strip()
            authors_line = htmlmod.unescape(re.sub(r"<[^>]+>", " ", rest))
            authors_line = re.sub(r"\s+", " ", authors_line)[:300]
            if "fordyce" not in authors_line.lower():
                continue
            target = href
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            if qs.get("url"):
                target = qs["url"][0]
            found.append({"title": title.strip(), "authors_line": authors_line.strip(), "url": target})
    # de-dupe by title
    seen, out = set(), []
    for f in found:
        k = norm_title(f["title"])
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out


def crossref_lookup_title(title):
    try:
        d = get_json("https://api.crossref.org/works?" + urllib.parse.urlencode(
            {"query.bibliographic": title, "rows": "3", "mailto": CFG["contact_email"]}), retries=2)
    except Exception:
        return ""
    for it in d.get("message", {}).get("items", []):
        t = (it.get("title") or [""])[0]
        if title_sim(t, title) >= 0.85:
            return norm_doi(it.get("DOI"))
    return ""


# --------------------------------------------------------------------------- site + portal context
def site_entries():
    """Entries currently on the website (publications.json in the live page's format), adapted
    to the fields the matching code uses."""
    out = []
    for p in load_pubs().get("publications", []):
        out.append({"id": norm_doi(p.get("doi")) or p.get("id", ""), "title_text": p.get("title", ""),
                    "is_preprint": p.get("type") == "preprint", "links": p.get("links", []),
                    "first_author": first_author_of(p.get("authors_html")), "year": p.get("year"),
                    "venue_text": p.get("venue", "")})
    return out


def find_site_entry(entries, doi=None, title=None, want_preprint=None, thresh=0.8):
    best, score = None, 0
    for e in entries:
        if doi and e.get("id") == doi:
            return e, 1.0
        if doi and doi in [doi_from_url(l.get("url", "")) for l in e.get("links", [])]:
            return e, 1.0
    if title:
        for e in entries:
            if want_preprint is not None and bool(e.get("is_preprint")) != want_preprint:
                continue
            s = title_sim(title, e.get("title_text") or "")
            if s > score:
                best, score = e, s
    if score >= thresh:
        return best, score
    return None, 0


def portal_keys():
    try:
        rows = json.load(open(CFG["data_portal_dir"] + "/data.json"))
        return sorted(set(r.get("p", "") for r in rows))
    except Exception:
        return []


def check_squarespace_flags(state):
    flags = state.setdefault("flags", {})
    try:
        body, _, _ = http(CFG["site_pubs_url"], timeout=30, retries=1)
        flags["pubs_switched"] = b'id="fl-pubs"' in body
    except Exception:
        pass
    try:
        body, _, _ = http(CFG["site_data_url"], timeout=30, retries=1)
        flags["data_switched"] = b'id="fdp-stats"' in body
    except Exception:
        pass
    return flags


# --------------------------------------------------------------------------- main
def merge_cands(lists):
    out = {}
    for lst in lists:
        for c in lst:
            k = c["id"]
            if k not in out:
                out[k] = c
                continue
            o = out[k]
            o["sources"] = sorted(set(o["sources"]) | set(c["sources"]))
            o["verified"] = o["verified"] or c["verified"]
            for f in ("pdf_url", "date", "year", "venue", "preprint"):
                if not o.get(f) and c.get(f):
                    o[f] = c[f]
            if len(c.get("authors", [])) > len(o.get("authors", [])):
                o["authors"] = c["authors"]
            for dl in c.get("data_links", []):
                if dl["url"] not in [x["url"] for x in o["data_links"]]:
                    o["data_links"].append(dl)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--lookback", type=int, default=CFG["lookback_days"])
    ap.add_argument("--no-scholar", action="store_true")
    args = ap.parse_args()

    state = load_json(STATE, {"seen": [], "flags": {}})
    seen = set(state.get("seen", []))
    done = load_json(DONE, {})
    entries = site_entries()
    site_dois = set(e.get("id") for e in entries)
    for e in entries:
        for l in e.get("links", []):
            d = doi_from_url(l.get("url", ""))
            if d:
                site_dois.add(d)

    lists = []
    try:
        oa = [cand_from_openalex(w) for w in openalex_recent(args.lookback)]
        log("OpenAlex: %d works in window" % len(oa))
        lists.append(oa)
    except Exception as e:
        log("OpenAlex failed: %s" % e)
    try:
        pm = pubmed_recent(args.lookback)
        log("PubMed: %d works in window" % len(pm))
        lists.append(pm)
    except Exception as e:
        log("PubMed failed: %s" % e)
    if CFG.get("arxiv", True):
        try:
            ax = arxiv_recent()
            log("arXiv: %d works by Polly" % len(ax))
            lists.append(ax)
        except Exception as e:
            log("arXiv failed: %s" % e)
    if CFG.get("scholar_alerts", True) and not args.no_scholar:
        sc = []
        for f in scholar_alerts():
            doi = crossref_lookup_title(f["title"])
            if doi:
                w = openalex_by_doi(doi)
                if w:
                    c = cand_from_openalex(w)
                    c["sources"] = ["Google Scholar alert"]
                    sc.append(c)
                    continue
            sc.append({"id": "t:" + slug(f["title"]), "doi": "", "title": f["title"], "year": None, "date": "",
                       "type": "unknown", "venue": "", "authors": [{"name": n.strip(), "corresponding": False}
                                                                  for n in f["authors_line"].split(" - ")[0].split(",") if n.strip()],
                       "web_url": f["url"], "pdf_url": "", "preprint": None, "data_links": [],
                       "sources": ["Google Scholar alert"], "verified": False})
        log("Scholar alerts: %d items naming Fordyce" % len(sc))
        lists.append(sc)

    cands = merge_cands(lists)

    # Enrich PubMed/arXiv-only DOIs with OpenAlex metadata (corresponding authors, preprint links, data deposits)
    for k, c in list(cands.items()):
        if c.get("doi") and "OpenAlex" not in c["sources"]:
            w = openalex_by_doi(c["doi"])
            if w:
                oc = cand_from_openalex(w)
                oc["sources"] = sorted(set(c["sources"]) | {"OpenAlex"})
                oc["verified"] = oc["verified"] or c["verified"]
                cands[k] = oc

    pkeys = portal_keys()
    new_items, refreshed = [], 0
    pending = {it["id"]: it for it in load_json(PENDING, [])}
    for k, c in cands.items():
        if k in done:
            continue
        if c.get("doi") and c["doi"] in site_dois:
            continue                     # already on the website
        if c.get("skip"):
            log("  skip (%s/%s): %s" % (c.get("type"), c.get("venue"), c["title"][:60]))
            continue
        if c.get("authors") and not any(is_polly(a["name"]) for a in c["authors"]):
            log("  skip (no Polly Fordyce in author list): %s" % c["title"][:60])
            continue
        # pair with what is on the site
        if c["type"] == "article":
            pre_doi = (c.get("preprint") or {}).get("doi")
            ent, sc_ = find_site_entry(entries, doi=pre_doi) if pre_doi else (None, 0)
            if not ent:
                ent, sc_ = find_site_entry(entries, title=c["title"], want_preprint=True)
            if ent:
                c["merge_into"] = ent["id"]
                c["merge_note"] = "The bioRxiv version is already on the site (%s, %s). Approving merges both into ONE entry with links to both." % (
                    ent.get("first_author", "?"), ent.get("year", "?"))
                pre_links = {l["label"].lower(): l["url"] for l in ent.get("links", [])}
                pre = c.get("preprint") or {"doi": ent["id"], "venue": ent.get("venue_text") or "bioRxiv"}
                # prefer the links already on the site (that PDF is hosted and known to work)
                if pre_links.get("pdf"):
                    pre["pdf"] = pre_links["pdf"]
                if pre_links.get("web"):
                    pre["web"] = pre_links["web"]
                pre.setdefault("doi", ent["id"])
                c["preprint"] = pre
        if c.get("year") and c["year"] < datetime.date.today().year - 1:
            c["age_note"] = "Published in %s but only just indexed - either it was missing from the site or it is a mis-attribution. Check before saying yes." % c["year"]
        elif c["type"] == "preprint":
            ent, sc_ = find_site_entry(entries, title=c["title"], want_preprint=False)
            if ent:
                c["merge_into"] = ent["id"]
                c["merge_note"] = "The published version is already on the site; approving adds the preprint links to that entry."
        # existing portal study key for this paper (so a rename can be offered)
        fa = c["authors"][0]["name"] if c.get("authors") else ""
        sur = fmt_author(fa).split(",")[0] if fa else ""
        c["portal_existing_keys"] = [p for p in pkeys if sur and p.lower().startswith(sur.lower())
                                     and (str(c.get("year") or "") in p or str((c.get("year") or 0) - 1) in p)]
        # proposed entry html (dashboard shows it; publish.py rebuilds it with hosted PDF links)
        ah, notes = authors_html(c["authors"])
        c["authors_html"] = ah
        c["author_notes"] = notes
        c["first_author"] = sur
        c["seen_first"] = pending.get(k, {}).get("seen_first") or today()
        if k in pending:
            keep = pending[k]
            for f in ("status", "portal_status", "portal_proposal", "website_status", "seen_first", "pdf_status"):
                if keep.get(f) is not None:
                    c[f] = keep[f]
            refreshed += 1
        else:
            c["status"] = "awaiting your decision"
            new_items.append(c)
        pending[k] = c
        seen.add(k)

    flags = check_squarespace_flags(state)
    log("new: %d, refreshed: %d, pending total: %d, site entries: %d, squarespace flags: %s" % (
        len(new_items), refreshed, len(pending), len(entries), flags))
    for c in new_items:
        log("  NEW %s | %s | %s (%s) | %s | verified=%s%s" % (c["type"], c["id"], c["venue"], c.get("year"),
            c["title"][:70], c["verified"], " | merges into " + c["merge_into"] if c.get("merge_into") else ""))
    if args.dry_run:
        print(json.dumps(new_items, indent=1, ensure_ascii=False)[:6000])
        return
    update_pending(lambda items: list(pending.values()))
    state["seen"] = sorted(seen)
    state["last_poll"] = today()
    state["last_poll_new"] = [c["id"] for c in new_items]
    save_json(STATE, state)


if __name__ == "__main__":
    main()
