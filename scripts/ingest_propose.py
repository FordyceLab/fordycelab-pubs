#!/usr/bin/env python3
"""ingest_propose.py <pending-id> - download a paper's data repository and have Claude draft
rows for the Data Portal (proposal.csv + proposal.md + proposal.json). Nothing is merged
until Polly approves on the dashboard (portal_merge.py).

Repos understood: OSF (osf.io/xxxxx), Zenodo (zenodo.org/records/N or DOI 10.5281/zenodo.N),
GitHub repos, figshare, or any direct file URL.
"""
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import zipfile
from pathlib import Path

from common import (CFG, DECISIONS, INGEST, PORTAL_FIELDS, anthropic_key, download, get_json, http, load_json, log,
                    now_label, pending_by_id, slug, update_pending)

MAX_FILE = 80 * 1024 * 1024
MAX_TOTAL = 600 * 1024 * 1024
SKIP_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".mp4", ".mov", ".ai", ".psd", ".pdf", ".pptx", ".docx",
            ".nd2", ".czi", ".fastq", ".fq", ".bam", ".fast5", ".h5", ".hdf5", ".npy", ".pkl", ".pickle"}


def set_status(pid, **fields):
    def f(items):
        for it in items:
            if it["id"] == pid:
                it.update(fields)
    update_pending(f)


# --------------------------------------------------------------------------- downloaders
def dl_file(url, dest, total):
    if total[0] > MAX_TOTAL:
        return False
    try:
        p, ct = download(url, dest, timeout=600)
        total[0] += p.stat().st_size
        if p.stat().st_size > MAX_FILE and p.suffix.lower() not in (".zip", ".gz", ".csv", ".tsv", ".xlsx", ".json", ".txt"):
            p.unlink()
            return False
        if p.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(p) as z:
                    for n in z.namelist():
                        if Path(n).suffix.lower() in SKIP_EXT or n.endswith("/"):
                            continue
                        z.extract(n, p.parent / (p.stem + "_unzipped"))
            except Exception as e:
                log("unzip failed for %s: %s" % (p.name, e))
        return True
    except Exception as e:
        log("download failed %s: %s" % (url, e))
        return False


def osf_node(url):
    m = re.search(r"osf\.io/([a-z0-9]{5})", url, re.I)
    return m.group(1) if m else ""


def fetch_osf(node, dest, total, depth=0):
    url = "https://api.osf.io/v2/nodes/%s/files/osfstorage/?page[size]=100" % node
    _walk_osf(url, dest, total, depth)
    # child components too
    try:
        ch = get_json("https://api.osf.io/v2/nodes/%s/children/?page[size]=50" % node)
        for c in ch.get("data", []):
            fetch_osf(c["id"], dest / ("component_" + c["id"]), total, depth + 1)
    except Exception:
        pass


def _walk_osf(url, dest, total, depth):
    if depth > 6:
        return
    while url:
        d = get_json(url)
        for f in d.get("data", []):
            attrs, links = f.get("attributes", {}), f.get("links", {})
            name = attrs.get("name", "file")
            if attrs.get("kind") == "folder":
                fl = f.get("relationships", {}).get("files", {}).get("links", {}).get("related", {})
                href = fl.get("href") if isinstance(fl, dict) else fl
                if href:
                    _walk_osf(href, dest / slug(name, 40), total, depth + 1)
            elif links.get("download"):
                if Path(name).suffix.lower() in SKIP_EXT:
                    continue
                dl_file(links["download"], dest / name, total)
        url = d.get("links", {}).get("next")


def fetch_zenodo(url, dest, total):
    m = re.search(r"zenodo\.(\d+)", url) or re.search(r"zenodo\.org/records?/(\d+)", url)
    if not m:
        return False
    rec = get_json("https://zenodo.org/api/records/%s" % m.group(1))
    for f in rec.get("files", []):
        name = f.get("key") or f.get("filename") or "file"
        if Path(name).suffix.lower() in SKIP_EXT:
            continue
        link = (f.get("links") or {}).get("self") or (f.get("links") or {}).get("download")
        if link:
            dl_file(link, dest / name, total)
    (dest / "zenodo_record.json").write_text(json.dumps(rec.get("metadata", {}), indent=1))
    return True


def fetch_github(url, dest, total):
    m = re.search(r"github\.com/([^/]+)/([^/#?]+)", url)
    if not m:
        return False
    owner, repo = m.group(1), m.group(2).replace(".git", "")
    for branch in ("main", "master"):
        try:
            zip_url = "https://github.com/%s/%s/archive/refs/heads/%s.zip" % (owner, repo, branch)
            if dl_file(zip_url, dest / ("%s-%s.zip" % (repo, branch)), total):
                return True
        except Exception:
            continue
    return False


def fetch_figshare(url, dest, total):
    m = re.search(r"figshare\.com/(?:articles|collections)/[^/]+/(\d+)", url) or re.search(r"figshare\.com/.*?(\d{6,})", url)
    if not m:
        return False
    try:
        art = get_json("https://api.figshare.com/v2/articles/%s" % m.group(1))
        for f in art.get("files", []):
            if Path(f["name"]).suffix.lower() in SKIP_EXT:
                continue
            dl_file(f["download_url"], dest / f["name"], total)
        return True
    except Exception:
        # collection: list articles
        try:
            arts = get_json("https://api.figshare.com/v2/collections/%s/articles?page_size=100" % m.group(1))
            for a in arts:
                art = get_json("https://api.figshare.com/v2/articles/%s" % a["id"])
                for f in art.get("files", []):
                    if Path(f["name"]).suffix.lower() in SKIP_EXT:
                        continue
                    dl_file(f["download_url"], dest / slug(art.get("title", "a"), 30) / f["name"], total)
            return True
        except Exception:
            return False


def fetch_repo(url, dest):
    total = [0]
    u = url.lower()
    if "osf.io" in u:
        node = osf_node(url)
        if node:
            fetch_osf(node, dest, total)
            return "osf"
    if "zenodo" in u:
        if fetch_zenodo(url, dest, total):
            return "zenodo"
    if "github.com" in u:
        if fetch_github(url, dest, total):
            return "github"
    if "figshare" in u:
        if fetch_figshare(url, dest, total):
            return "figshare"
    name = os.path.basename(urllib.parse.urlparse(url).path) or "download.bin"
    dl_file(url, dest / name, total)
    return "direct"


# --------------------------------------------------------------------------- Claude
def portal_context():
    try:
        rows = json.load(open(CFG["data_portal_dir"] + "/data.json"))
    except Exception:
        rows = []
    import collections
    ctx = {
        "existing_p_keys": sorted(set(r.get("p", "") for r in rows)),
        "existing_m": collections.Counter(r.get("m", "") for r in rows).most_common(),
        "existing_mt": collections.Counter(r.get("mt", "") for r in rows).most_common(),
        "existing_pw": sorted(set(r.get("pw", "") for r in rows)),
        "existing_u": collections.Counter(r.get("u", "") for r in rows).most_common(30),
        "existing_lim": collections.Counter(r.get("lim", "") for r in rows).most_common(),
        "sample_rows": rows[:3] + rows[10000:10003] + rows[-3:],
    }
    return ctx


PROMPT = """You are preparing rows for the Fordyce Lab Data Portal (fordycelab.com/data) from a paper's
deposited data. Work ONLY inside this directory. The raw downloaded files are under ./raw/.

PAPER: {title}
Authors: {authors}
Venue/year: {venue} ({year}); DOI {doi}
Data repository: {repo}

PORTAL ROW SCHEMA (one row = one quantitative measurement of one protein variant), CSV columns in this order:
  p   = study key, format <FirstAuthorSurname><Year>_<VenueAbbrev>[_<Protein>]  (examples in context.json: existing_p_keys)
  y   = year
  pw  = wild-type protein / protein system name (reuse an existing_pw spelling if it is the same protein)
  pm  = variant / mutant label, e.g. Q21G, WT, or a domain/peptide id
  lib = library / scan name (e.g. Glycine, Alanine, clinical variants) or blank
  lig = ligand / substrate / DNA sequence / peptide
  mt  = measurement type: one of existing_mt if it fits (Kd, DDG, kcat/KM, Ki, IC50, kcat, KM, koff, kon, fraction_active, EC50, fraction_native, DG_native, k_unfold), else a new short key
  v   = value (number as in the paper, scientific notation ok)
  u   = units (M, uM, nM, s-1, M-1s-1, kcal/mol, ...)
  e   = error/uncertainty (blank if none), et = error type (sd, sem, ci95, blank)
  lim = "none" if a real value; "upper"/"lower" if the value is a detection-limit bound
  pv  = p-value or blank
  m   = method/technology (reuse existing_m spellings: HT-MEK, STAMMP, STAMMPPING, MRBLE-pep, MITOMI, SPARKfold ..., or the paper's technology name)
  n   = short note (blank ok)

TASK:
1. Read context.json (schema examples, existing keys) and inventory ./raw (ls, head, python3/pandas-free parsing with csv/openpyxl if present; if xlsx cannot be read, say so).
2. Identify the file(s) holding per-variant quantitative measurements. Ignore raw images, traces, and fits unless summary tables are absent.
3. Write proposal.csv with the header row exactly: {fields}
   Include EVERY per-variant measurement of the types above (kinetic and thermodynamic constants, binding affinities,
   IC50/EC50, fractions active/native, unfolding rates). Do NOT include raw fluorescence, read counts, or per-replicate traces.
   Use one consistent p key for this paper (if the existing_p_keys already contain this paper's preprint key, REUSE it exactly).
4. Write proposal.json: {{"p": "<the p key>", "desc": "<one-line description of what was measured, in the style of the existing portal>", "badge": "<e.g. 2,705 kinetics/IC50>", "technology": "<m>", "protein": "<pw>", "rows": <int>}}
5. Write proposal.md: a short report for Polly - which files you used, how many rows, measurement types and counts,
   units and any conversions, columns you could NOT map or were unsure about, and any questions. Be honest about uncertainty.
Finish with proposal.csv, proposal.json and proposal.md present. Do not modify anything under raw/.
"""


def run_claude(workdir, item, repo):
    key = anthropic_key()
    if not key:
        raise RuntimeError("no Anthropic API key at %s" % CFG["anthropic_key_file"])
    env = dict(os.environ)
    env["ANTHROPIC_API_KEY"] = key
    env.pop("CLAUDECODE", None)
    prompt = PROMPT.format(title=item.get("title", ""), authors=", ".join(a["name"] for a in item.get("authors", [])),
                           venue=item.get("venue", ""), year=item.get("year", ""), doi=item.get("doi", ""), repo=repo,
                           fields=",".join(PORTAL_FIELDS))
    claude = "/opt/homebrew/bin/claude" if os.path.exists("/opt/homebrew/bin/claude") else "claude"
    cmd = [claude, "-p", prompt, "--model", CFG.get("claude_model", "claude-sonnet-5"), "--max-turns", "80",
           "--permission-mode", "acceptEdits",
           "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash(python3:*),Bash(ls:*),Bash(head:*),Bash(wc:*),Bash(file:*),Bash(cat:*),Bash(unzip:*),Bash(gunzip:*),Bash(find:*)"]
    r = subprocess.run(cmd, cwd=str(workdir), env=env, capture_output=True, text=True, timeout=45 * 60)
    (workdir / "claude_output.txt").write_text(r.stdout + "\n--- stderr ---\n" + r.stderr)
    if r.returncode != 0:
        raise RuntimeError("claude exited %d: %s" % (r.returncode, r.stderr[-400:]))


def main():
    pid = sys.argv[1]
    item = pending_by_id().get(pid)
    if not item:
        raise SystemExit("no pending item " + pid)
    dec = load_json(DECISIONS, {}).get(pid, {})
    repo = dec.get("repo_url") or ", ".join(d["url"] for d in item.get("data_links", []))
    workdir = INGEST / slug(pid, 60)
    raw = workdir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    try:
        kinds = []
        for u in [x.strip() for x in re.split(r"[\s,;]+", repo) if x.strip()]:
            set_status(pid, portal_status="downloading %s" % u)
            kinds.append(fetch_repo(u, raw / slug(u, 40)))
        nfiles = sum(1 for _ in raw.rglob("*") if _.is_file())
        if nfiles == 0:
            set_status(pid, portal_status="download failed - no files found at %s (check the URL)" % repo)
            return
        (workdir / "context.json").write_text(json.dumps(portal_context(), indent=1))
        set_status(pid, portal_status="Claude is reading %d files and drafting the proposal..." % nfiles)
        run_claude(workdir, item, repo)
        csvp, mdp, jp = workdir / "proposal.csv", workdir / "proposal.md", workdir / "proposal.json"
        if not csvp.exists():
            set_status(pid, portal_status="proposal failed - Claude did not produce proposal.csv (see %s)" % (workdir / "claude_output.txt"))
            return
        import csv
        with open(csvp, newline="") as f:
            rows = list(csv.DictReader(f))
        meta = load_json(jp, {})
        summary = mdp.read_text()[:6000] if mdp.exists() else "(no proposal.md)"
        set_status(pid, portal_status="proposal ready - review and approve on the dashboard",
                   portal_proposal={"rows": len(rows), "p": meta.get("p", ""), "desc": meta.get("desc", ""),
                                    "badge": meta.get("badge", ""), "summary": summary, "csv": str(csvp),
                                    "types": sorted(set(r.get("mt", "") for r in rows)), "ts": now_label()})
        log("proposal ready for %s: %d rows" % (pid, len(rows)))
    except Exception as e:
        set_status(pid, portal_status="proposal failed: %s" % str(e)[:300])
        log("ingest ERROR %s: %s" % (pid, e))


if __name__ == "__main__":
    main()
