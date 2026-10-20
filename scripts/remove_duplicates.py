#!/usr/bin/env python3
"""remove_duplicates.py <dup-id-or-doi>=<keep-doi> ... [--no-push]
Remove a duplicate entry from publications.json, moving any links it had that the kept entry
lacks (e.g. "OSF data") onto the kept entry; re-point the pending/done records; renumber; push.
Example:
  remove_duplicates.py 10.17605/osf.io/cfsdg=10.64898/2026.02.24.707783 W7167300566=10.64898/2026.05.01.722333
"""
import sys

from common import (CFG, DECISIONS, DONE, PENDING, PUBS, ROOT, git_commit_push, load_json, load_pubs, locked, log,
                    norm_doi, now_label, renumber, save_json, today)

if "--no-push" in sys.argv:
    CFG["git_push"] = False
pairs = [a.split("=", 1) for a in sys.argv[1:] if "=" in a]


def match(e, key):
    key = key.lower()
    return (e.get("doi") or "").lower() == norm_doi(key) or (e.get("id") or "").lower().endswith(key) or (e.get("id") or "").lower() == key


pubs = load_pubs()
with locked(PENDING):
    pending = load_json(PENDING, [])
    done = load_json(DONE, {})
    dec = load_json(DECISIONS, {})
    for dup_key, keep_key in pairs:
        dup = next((e for e in pubs["publications"] if match(e, dup_key)), None)
        keep = next((e for e in pubs["publications"] if match(e, keep_key)), None)
        if not dup or not keep:
            log("skip %s=%s (dup found: %s, keep found: %s)" % (dup_key, keep_key, bool(dup), bool(keep)))
            continue
        have = {l["url"].rstrip("/").lower() for l in keep["links"]}
        moved = []
        for l in dup.get("links", []):
            if l["label"].lower() in ("web", "pdf"):
                if "osf.io" in l["url"] and not any("osf.io" in u for u in have):
                    keep["links"].append({"label": "OSF data", "url": l["url"]})
                    moved.append("OSF data")
                continue
            if l["url"].rstrip("/").lower() not in have:
                keep["links"].append(l)
                moved.append(l["label"])
        pubs["publications"] = [e for e in pubs["publications"] if e is not dup]
        log("removed duplicate '%s' (%s); kept %s; moved links: %s" % (dup["title"][:50], dup_key, keep.get("doi"), moved or "none"))
        for it in pending:
            if match({"doi": it.get("doi", ""), "id": it.get("id", "")}, dup_key):
                it["website_entry_id"] = keep.get("doi") or keep.get("id")
                it["merge_into"] = keep.get("doi")
                it["duplicate_of"] = keep.get("doi")
                it["website_status"] = "published"
                it["status"] = "duplicate of an existing entry - links merged into #%s; %s" % (keep.get("num", "?"), it.get("portal_status", ""))
        done[dup_key] = {"title": dup["title"], "website": "duplicate - merged into " + str(keep.get("doi")), "ts": now_label()}
    renumber(pubs)
    pubs["updated"] = today()
    save_json(PUBS, pubs)
    save_json(PENDING, pending)
    save_json(DONE, done)
print("entries now:", pubs["count"])
print("git:", git_commit_push(ROOT, [PUBS, PENDING, DONE], "Remove duplicate entries, merge their links (%s)" % today())[2])
