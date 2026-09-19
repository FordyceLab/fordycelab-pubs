#!/usr/bin/env python3
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
    if work.get("type") == "preprint":
        return True
    src = ((work.get("primary_location") or {}).get("source") or {})
    name = (src.get("display_name") or "").lower()
    return "biorxiv" in name or "arxiv" in name or "medrxiv" in name or "preprint" in name


def venue_name(work):
    src = ((work.get("primary_location") or {}).get("source") or {})
    return src.get("display_name") or ""


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
    for w in works:
        preprint = is_preprint(w)
        if preprint and not INCLUDE_PREPRINTS:
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

        entries.append(entry)

    # Sort: pinned first, then by year desc, then title.
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
