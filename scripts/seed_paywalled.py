#!/usr/bin/env python3
"""seed_paywalled.py - one-time: queue every published entry whose PDF button points at a
publisher site (not hosted by us) as a "waiting for your PDF" card on the dashboard, so Polly
can upload her copy and have the PDF button switched to the hosted file.
Safe to re-run (skips entries already queued or done)."""
import re

from common import DECISIONS, DONE, PENDING, first_author_of, load_json, load_pubs, locked, log, now_label, save_json, today

OURS = ("www.fordycelab.com", "raw.githubusercontent.com", "fordycelab.github.io", "arxiv.org")


def main():
    pubs = load_pubs()
    with locked(PENDING):
        items = load_json(PENDING, [])
        dec = load_json(DECISIONS, {})
        done = load_json(DONE, {})
        have = {i["id"] for i in items}
        n = 0
        for e in pubs["publications"]:
            if e.get("type") == "preprint" or not e.get("doi"):
                continue
            pdf = next((l for l in e["links"] if l["label"] == "PDF"), None)
            if not pdf:
                continue
            host = re.sub(r"^https?://([^/]+)/.*", r"\1", pdf["url"])
            if host in OURS:
                continue
            pid = e["doi"]
            if pid in have:
                continue
            done.pop(pid, None)
            it = {"id": pid, "doi": pid, "type": "article", "title": e["title"], "year": e.get("year"), "venue": e.get("venue", ""),
                  "first_author": first_author_of(e.get("authors_html")), "authors": [], "web_url": next((l["url"] for l in e["links"] if l["label"] == "web"), ""),
                  "pdf_url": "", "pdf_candidates": [], "preprint": None, "data_links": [], "sources": ["site: PDF on publisher site"],
                  "verified": True, "merge_into": None, "website_status": "published", "website_entry_id": pid,
                  "pdf_publisher_url": pdf["url"], "pdf_expected": None,
                  "pdf_status": "the PDF button points at the publisher's site (%s) - upload your copy here to host it on the lab site" % host,
                  "status": "website: published; PDF on publisher site", "seen_first": today()}
            from publish import pdf_slug
            it["pdf_expected"] = pdf_slug(it) + ".pdf"
            items.append(it)
            dec.setdefault(pid, {"website": True, "portal": False, "repo_url": "", "website_applied": now_label(), "ts": now_label()})
            n += 1
            log("queued for PDF upload: %s | %s | expected file %s" % (pid, e["title"][:50], it["pdf_expected"]))
        save_json(PENDING, items)
        save_json(DECISIONS, dec)
        save_json(DONE, done)
    print("queued", n)


if __name__ == "__main__":
    main()
