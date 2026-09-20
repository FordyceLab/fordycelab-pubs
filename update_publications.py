#!/usr/bin/env python3
import re
"""
update_publications.py
----------------------
Fetches all works for the Fordyce Lab from OpenAlex, merges in manual
enrichments from overrides.json, and writes publications.json.

That JSON is what the Squarespace Code Block reads to render the page.
Nothing here touches Squarespace directly.

Data source:  OpenAlex (https://openalex.org) -- free, no API key.
Author:       Polly M. Fordyce  ->  OpenAlex ID A5085863617

Usage:
    python3 update_publications.py
    python3 update_publications.py --dry-run     # print, don't write
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OPENALEX_AUTHOR_ID = "A5085863617"        # Polly M. Fordyce
# Set this to your email -- OpenAlex uses it to put you in their faster,
# more polite request pool. Just courtesy; no signup needed.
CONTACT_EMAIL = "fordyce@stanford.edu"

# Include preprints (bioRxiv, etc.) in the list? (You said: yes.)
INCLUDE_PREPRINTS = True

# Only include works from this year onward. Set to None for the entire career.
FROM_YEAR = None

HERE = Path(__file__).resolve().parent
OVERRIDES_PATH = HERE / "overrides.json"
OUTPUT_PATH = HERE / "publications.json"

OPENALEX_BASE = "https://api.openalex.org/works"


# ---------------------------------------------------------------------------
# OpenAlex fetch
# ---------------------------------------------------------------------------

import importlib.util as _ilu
_es = _ilu.spec_from_file_location("enrich_links", str(HERE / "enrich_links.py"))
enrich_links = _ilu.module_from_spec(_es); _es.loader.exec_module(enrich_links)

def fetch_all_works():
    """Page through every OpenAlex work authored by the lab PI."""
    works = []
    cursor = "*"
    filters = [f"author.id:{OPENALEX_AUTHOR_ID}"]
    if FROM_YEAR:
        filters.append(f"from_publication_date:{FROM_YEAR}-01-01")

    while cursor:
        params = {
            "filter": ",".join(filters),
            "sort": "publication_date:desc",
            "per-page": "200",
            "cursor": cursor,
            "mailto": CONTACT_EMAIL,
        }
        url = OPENALEX_BASE + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": f"fordycelab-pubs/1.0 ({CONTACT_EMAIL})"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)

        works.extend(data.get("results", []))
        cursor = data.get("meta", {}).get("next_cursor")
        if cursor:
            time.sleep(0.2)  # be polite

    return works


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_author(display_name):
    """'Polly M. Fordyce' -> 'Fordyce, P.M.'  (best-effort)."""
    if not display_name:
        return ""
    parts = display_name.replace(".", "").split()
    if len(parts) == 1:
        return parts[0]
    surname = parts[-1]
    initials = "".join(p[0].upper() + "." for p in parts[:-1])
    return f"{surname}, {initials}"


def build_author_html(work):
    """Author string with the PI bolded. Lab members can be emphasized
    later via overrides if desired."""
    names = []
    for a in work.get("authorships", []):
        author = a.get("author", {})
        formatted = format_author(author.get("display_name", ""))
        if (author.get("id") or "").endswith(OPENALEX_AUTHOR_ID):
            formatted = f"<strong>{formatted}</strong>"
        names.append(formatted)
    return ", ".join(names)


def is_preprint(work):
    name = (((work.get("primary_location") or {}).get("source") or {}).get("display_name") or "").lower()
    if any(k in name for k in ("biorxiv", "medrxiv", "arxiv", "chemrxiv", "ssrn", "research square")):
        return True
    if work.get("type") == "preprint" and (not name or "osf" in name):
        return True
    return False



# Non-CS conference / meeting ABSTRACTS (Biophysical Society, FASEB, etc.) are
# posters/talks, not papers -- Polly does not want them on the feed. CS
# conference papers (ACM / IEEE / NeurIPS / ICML ...) ARE kept. Force-keep a
# false positive with {"keep": true} in overrides.json.
CS_ABSTRACT_KEEP = ("neurips", "neural information", "icml", "iclr", "aaai",
                    "cvpr", "usenix", "proceedings of machine learning")
MEETING_ABSTRACT_VENUES = ("biophysical journal", "the faseb journal")


def _cs_venue(work):
    doi = (work.get("doi") or "").lower()
    if "/10.1145/" in doi or "/10.1109/" in doi:   # ACM, IEEE = CS
        return True
    name = (((work.get("primary_location") or {}).get("source") or {}).get("display_name") or "").lower()
    return any(a in name for a in CS_ABSTRACT_KEEP)


def _is_osf_page(work):
    return (((work.get("primary_location") or {}).get("source") or {}).get("display_name") or "").strip().lower() == "osf preprints"


def _is_dataset(work):
    doi = (work.get("doi") or "").lower()
    return work.get("type") == "dataset" or any(pfx in doi for pfx in ("10.17605/osf.io", "10.5281/zenodo", "10.25740/"))


def is_meeting_abstract(work):
    if _cs_venue(work):
        return False
    name = (((work.get("primary_location") or {}).get("source") or {}).get("display_name") or "").lower()
    if work.get("type") == "conference-abstract":
        return True
    if name in MEETING_ABSTRACT_VENUES:
        return True
    return False

def venue_name(work):
    src = ((work.get("primary_location") or {}).get("source") or {})
    name = src.get("display_name") or ""
    low = name.lower()
    if "biorxiv" in low: return "bioRxiv"
    if "medrxiv" in low: return "medRxiv"
    if "chemrxiv" in low: return "ChemRxiv"
    if low == "arxiv" or "arxiv.org" in low: return "arXiv"
    if "research square" in low: return "Research Square"
    return re.sub(r"\s*\([^)]*\)\s*$", "", name).strip() or name


def best_links(work):
    """Auto-detected links from OpenAlex: web (DOI) and open-access PDF."""
    links = []
    doi = work.get("doi")
    if doi:
        links.append({"label": "web", "url": doi})

    # Open-access PDF, if OpenAlex knows one.
    oa = work.get("best_oa_location") or {}
    pdf_url = oa.get("pdf_url")
    if pdf_url:
        links.append({"label": "PDF", "url": pdf_url})

    return links


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------

def load_overrides():
    if not OVERRIDES_PATH.exists():
        return {}
    with open(OVERRIDES_PATH) as f:
        raw = json.load(f)
    # Key by normalized DOI, OpenAlex ID, or short OpenAlex ID (W...).
    norm = {}
    for key, val in raw.items():
        if key.startswith("_"):        # skip _README and other comments
            continue
        norm[normalize_key(key)] = val
    return norm


def normalize_key(key):
    key = key.strip()
    if key.startswith("http") and "openalex.org" in key:
        return key.rsplit("/", 1)[-1].upper()   # -> W3196281287
    if key.upper().startswith("W") and key[1:].isdigit():
        return key.upper()
    return normalize_doi(key)


def normalize_doi(doi):
    if not doi:
        return ""
    return doi.lower().replace("https://doi.org/", "").strip()


def apply_override(entry, ov):
    """Merge a manual override onto an auto-generated entry.

    Supported override fields (all optional):
      "hide": true                      -> drop this paper entirely
      "pin": true                       -> sort to the very top
      "title": "..."                    -> replace title
      "venue": "..."                    -> replace journal/venue
      "authors_html": "..."             -> replace the whole author string
                                           (use for * equal-contrib, ‡ etc.)
      "pdf": "https://.../paper.pdf"    -> hosted-PDF button (adds/replaces PDF)
      "links_add": [ {"label": "...",   -> extra buttons: data, GitHub, bioRxiv,
                      "url": "..."} ]       OSF, Zenodo, commentary, etc.
      "note": "..."                     -> small line under the entry
    """
    if ov.get("hide"):
        return None
    if "title" in ov:
        entry["title"] = ov["title"]
    if "venue" in ov:
        entry["venue"] = ov["venue"]
    if "authors_html" in ov:
        entry["authors_html"] = ov["authors_html"]
    if "note" in ov:
        entry["note"] = ov["note"]
    if "news" in ov:
        entry["news"] = ov["news"]
    if ov.get("pin"):
        entry["pin"] = True

    if "pdf" in ov:
        # Replace any auto PDF with the hosted one.
        entry["links"] = [l for l in entry["links"] if l["label"] != "PDF"]
        entry["links"].append({"label": "PDF", "url": ov["pdf"]})

    for extra in ov.get("links_add", []):
        entry["links"].append(extra)

    return entry


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_entries():
    works = fetch_all_works()
    overrides = load_overrides()

    entries = []
    _cache = enrich_links.load_cache()
    for w in works:
        preprint = is_preprint(w)
        if preprint and not INCLUDE_PREPRINTS:
            continue

        if not w.get("doi"):   # OSF project pages / junk records without a DOI
            continue

        # Drop non-CS conference/meeting abstracts unless an override force-keeps them.
        _doi_key = normalize_doi(w.get("doi"))
        _sid = (w.get("id") or "").rsplit("/", 1)[-1].upper()
        _ov = overrides.get(_doi_key) or overrides.get(_sid) or {}
        if is_meeting_abstract(w) and not _ov.get("keep"):
            continue
        if (w.get("type") == "dissertation" or _is_dataset(w) or _is_osf_page(w)) and not _ov.get("keep"):
            continue

        entry = {
            "id": w.get("id"),
            "doi": normalize_doi(w.get("doi")),
            "title": w.get("display_name") or "(untitled)",
            "authors_html": build_author_html(w),
            "venue": venue_name(w),
            "year": w.get("publication_year"),
            "type": "preprint" if preprint else "article",
            "links": best_links(w),
            "pin": False,
        }

        short_id = (w.get("id") or "").rsplit("/", 1)[-1].upper()
        ov = overrides.get(entry["doi"]) or overrides.get(short_id)
        if ov:
            entry = apply_override(entry, ov)
        if entry is None:      # hidden
            continue

        entry = enrich_links.enrich_entry(entry, _cache)
        entries.append(entry)

    # Sort: pinned first, then by year desc, then title.
    enrich_links.save_cache(_cache)
    try:
        _add = json.load(open(HERE / "additions.json")).get("additions", [])
    except Exception:
        _add = []
    _have = {(e.get("doi") or "").lower() for e in entries}
    for _a in _add:
        if (_a.get("doi") or "").lower() in _have:
            continue
        _a = dict(_a); _a.setdefault("id", _a.get("doi")); _a.setdefault("pin", False)
        _a["links"] = enrich_links.order_links(_a.get("links", []))
        entries.append(_a)
    entries.sort(key=lambda e: (not e.get("pin"), -(e.get("year") or 0), e["title"].lower()))
    return entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print, don't write")
    args = ap.parse_args()

    entries = build_entries()
    payload = {
        "updated": time.strftime("%Y-%m-%d"),
        "count": len(entries),
        "publications": entries,
    }
    out = json.dumps(payload, indent=2, ensure_ascii=False)

    if args.dry_run:
        print(out)
        print(f"\n[dry-run] {len(entries)} publications (not written)", file=sys.stderr)
        return

    OUTPUT_PATH.write_text(out, encoding="utf-8")
    print(f"Wrote {len(entries)} publications to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
