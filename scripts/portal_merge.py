#!/usr/bin/env python3
"""portal_merge.py <pending-id> [--reject] [--rename OLD NEW] [--no-push]

Approve (or reject) a drafted portal proposal: append proposal.csv rows to the data-portal
data.json, record the paper's description in paper_meta.json, regenerate stats.json, commit
and push. With --rename, rows of an existing study key (e.g. Lee2026_bioRxiv_SHP2) are
re-keyed first (a preprint that got published).
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

from common import CFG, DECISIONS, DONE, INGEST, PENDING, PORTAL_FIELDS, ROOT, git_commit_push, load_json, log, now_label, pending_by_id, save_json, slug, update_pending


def set_status(pid, **fields):
    def f(items):
        for it in items:
            if it["id"] == pid:
                it.update(fields)
    update_pending(f)


def write_data_json(path, rows):
    """Keep the file compact (the page downloads it) but one row per line so git diffs stay readable."""
    with open(path, "w") as f:
        f.write("[\n")
        for i, r in enumerate(rows):
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")))
            f.write(",\n" if i < len(rows) - 1 else "\n")
        f.write("]\n")


def main():
    args = sys.argv[1:]
    pid = args[0]
    reject = "--reject" in args
    no_push = "--no-push" in args
    rename = None
    if "--rename" in args:
        i = args.index("--rename")
        rename = (args[i + 1], args[i + 2])
    if no_push:
        CFG["git_push"] = False
    item = pending_by_id().get(pid)
    if not item:
        raise SystemExit("no pending item " + pid)
    if reject:
        set_status(pid, portal_status="rejected", portal_proposal=None)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "publish.py"), "--sweep"], timeout=600)
        log("portal proposal rejected for %s" % pid)
        return
    prop = item.get("portal_proposal") or {}
    csvp = Path(prop.get("csv") or (INGEST / slug(pid, 60) / "proposal.csv"))
    if not csvp.exists():
        set_status(pid, portal_status="merge failed - proposal.csv missing")
        raise SystemExit("missing " + str(csvp))
    portal = Path(CFG["data_portal_dir"])
    data_path = portal / "data.json"
    rows = json.load(open(data_path))
    if rename:
        n = 0
        for r in rows:
            if r.get("p") == rename[0]:
                r["p"] = rename[1]
                n += 1
        log("renamed %d rows %s -> %s" % (n, rename[0], rename[1]))
    with open(csvp, newline="") as f:
        new_rows = []
        for r in csv.DictReader(f):
            row = {}
            for k in PORTAL_FIELDS:
                v = (r.get(k) or "").strip()
                if v != "":
                    row[k] = v
            if row.get("p") and row.get("mt") and row.get("v"):
                new_rows.append(row)
    existing = set(json.dumps(r, sort_keys=True) for r in rows)
    added = [r for r in new_rows if json.dumps(r, sort_keys=True) not in existing]
    rows.extend(added)
    write_data_json(data_path, rows)
    meta = load_json(portal / "paper_meta.json", {})
    pkey = prop.get("p") or (added[0]["p"] if added else "")
    if pkey:
        meta[pkey] = {"title": (item.get("first_author") or "") + " et al.", "year": item.get("year"),
                      "journal": item.get("venue", ""), "badge": prop.get("badge", ""), "desc": prop.get("desc", ""),
                      "doi": item.get("doi", "")}
        save_json(portal / "paper_meta.json", meta)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "stats.py"), str(portal)], check=True)
    committed, pushed, note = git_commit_push(portal, [data_path, portal / "stats.json", portal / "paper_meta.json"],
                                              "Add %s (%d rows) %s" % (pkey or pid, len(added), now_label()))
    log("portal merge %s: +%d rows (%d duplicates skipped); git: %s" % (pid, len(added), len(new_rows) - len(added), note))
    set_status(pid, portal_status="merged", portal_proposal=None,
               portal_merged={"rows": len(added), "p": pkey, "git": note, "ts": now_label()})
    subprocess.run([sys.executable, str(ROOT / "scripts" / "publish.py"), "--sweep"] + (["--no-push"] if no_push else []), timeout=600)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "notify.py"), "--portal", pid], timeout=120)


if __name__ == "__main__":
    main()
