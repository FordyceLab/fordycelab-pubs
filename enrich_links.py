#!/usr/bin/env python3
"""Automatic link enrichment for the publications feed, used by update_publications.py.

For each published work it:
  - finds the prior bioRxiv/medRxiv preprint via Crossref `has-preprint` relation, and
  - finds OSF / Zenodo / figshare data deposits via Europe PMC's text-mined data links.

Both are GAP-FILL only: a curated link in overrides.json always wins, and nothing is
duplicated. Results are cached in enrich-cache.json (keyed by DOI) so weekly runs are fast
and survive a transient API outage. Every network call is best-effort — failures never break
the build. Finally, links are de-duplicated and canonically ordered: web, PDF, bioRxiv, rest.
"""
import json, os, re, time, urllib.request, urllib.parse

HOME = os.path.expanduser("~")
CACHE = os.path.join(HOME, "lab-publications-site", "enrich-cache.json")
UA = {"User-Agent": "fordycelab-pubs/1.0 (mailto:fordyce@gmail.com)"}
PREPRINT_PREFIXES = ("10.1101/", "10.64898/", "10.21203/", "10.26434/")  # bioRxiv/medRxiv, Nov2025 bioRxiv, ResSquare, ChemRxiv


def _get(url):
    req = urllib.request.Request(url, headers=UA)
    return json.load(urllib.request.urlopen(req, timeout=30))


def load_cache():
    try:
        return json.load(open(CACHE))
    except Exception:
        return {}


def save_cache(c):
    try:
        json.dump(c, open(CACHE, "w"))
    except Exception:
        pass


def crossref_preprint(doi):
    """Return (label, url) for the paper's prior preprint, or None."""
    try:
        m = _get("https://api.crossref.org/works/%s?mailto=fordyce@gmail.com" % urllib.parse.quote(doi))["message"]
    except Exception:
        return None
    for item in (m.get("relation", {}) or {}).get("has-preprint", []):
        pid = (item.get("id") or "").lower().replace("https://doi.org/", "")
        if item.get("id-type", "").lower() == "doi" and any(pid.startswith(p) for p in PREPRINT_PREFIXES):
            label = "medRxiv" if pid.startswith("10.1101/") and "medrxiv" in pid else "bioRxiv"
            return [label, "https://doi.org/" + pid]
    return None


def _norm_repo(url):
    """Normalize an EPMC data link to (label, clean_url), or None if not OSF/Zenodo/figshare."""
    u = (url or "").lower()
    m = re.search(r"10\.17605/osf\.io/([a-z0-9]+)", u) or re.search(r"osf\.io/([a-z0-9]{4,})", u)
    if m and ("osf.io" in u):
        return ["OSF data", "https://osf.io/%s/" % m.group(1)]
    if "zenodo" in u:
        z = re.search(r"zenodo\.org/record[s]?/(\d+)", u) or re.search(r"zenodo\.(\d+)", u)
        return ["Zenodo", "https://zenodo.org/records/%s" % z.group(1)] if z else ["Zenodo", url]
    if "figshare" in u:
        return ["figshare", url]
    return None


def epmc_repos(doi):
    """Return a list of [label, url] OSF/Zenodo/figshare deposits text-mined by Europe PMC."""
    out = []
    try:
        s = _get("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(
            {"query": "DOI:" + doi, "format": "json", "resultType": "lite", "pageSize": "1"}))
        hits = s.get("resultList", {}).get("result", [])
        if not hits:
            return out
        src, pid = hits[0].get("source"), hits[0].get("id")
        if not (src and pid):
            return out
        dl = _get("https://www.ebi.ac.uk/europepmc/webservices/rest/%s/%s/datalinks?format=json" % (src, pid))
        seen = set()
        for c in dl.get("dataLinkList", {}).get("Category", []):
            for sec in c.get("Section", []):
                for lk in sec.get("Linklist", {}).get("Link", []):
                    tgt = lk.get("Target", {}) or {}
                    url = tgt.get("Url", "") or ((tgt.get("Identifier") or {}).get("IDURL", ""))
                    nr = _norm_repo(url)
                    if nr and nr[1].lower() not in seen:
                        seen.add(nr[1].lower())
                        out.append(nr)
    except Exception:
        return out
    return out


SINGLETON = {"web", "pdf", "biorxiv", "medrxiv", "preprint", "osf data", "osf", "zenodo", "figshare"}


def order_links(links):
    """De-dup by URL (and to one button per singleton label) and order:
    web, PDF, bioRxiv/medRxiv/preprint, then the rest (stable)."""
    seen_url, seen_lab, dedup = set(), set(), []
    for l in links:
        k = (l.get("url") or "").rstrip("/").lower()
        lab = (l.get("label") or "").lower()
        if not k or k in seen_url:
            continue
        if lab in SINGLETON and lab in seen_lab:
            continue
        seen_url.add(k); seen_lab.add(lab)
        dedup.append(l)
    rank = {"web": 0, "pdf": 1, "biorxiv": 2, "medrxiv": 2, "preprint": 2}
    return sorted(dedup, key=lambda l: rank.get((l.get("label") or "").lower(), 3))


def enrich_entry(entry, cache):
    doi = entry.get("doi")
    links = entry.get("links", [])
    if not doi:
        entry["links"] = order_links(links)
        return entry
    cd = cache.get(doi)
    if cd is None:
        cd = {"preprint": crossref_preprint(doi), "repos": epmc_repos(doi)}
        cache[doi] = cd
        time.sleep(0.12)  # be polite to the APIs
    labels = {(l.get("label") or "").lower() for l in links}
    # prior preprint — only if the entry has no preprint link yet (curated wins)
    if not (labels & {"biorxiv", "medrxiv", "preprint"}) and cd.get("preprint"):
        links.append({"label": cd["preprint"][0], "url": cd["preprint"][1]})
    # data repos — only if the entry has no data link yet (curated wins); add at most one
    if not (labels & {"osf data", "osf", "zenodo", "figshare", "data"}):
        for lab, url in cd.get("repos", [])[:1]:
            links.append({"label": lab, "url": url})
    entry["links"] = order_links(links)
    return entry
