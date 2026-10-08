#!/usr/bin/env python3
"""Write a dashboard notification when the publications feed changes.

Run from run_and_publish.sh BEFORE the commit — it compares the freshly regenerated
publications.json (working tree) against the last committed version (HEAD) and appends a
one-line activity entry to ~/cy/server/pubs-activity.json, which the Cy dashboard shows as a
dismissible "Publications feed" card. No change → writes nothing. Never raises into the publish.
"""
import json, os, subprocess, datetime
HOME = os.path.expanduser("~")
SITE = os.path.join(HOME, "lab-publications-site")
PUB = os.path.join(SITE, "publications.json")
ACT = os.path.join(HOME, "cy", "server", "pubs-activity.json")

def key(p): return (p.get("doi") or p.get("id") or p.get("title") or "").strip().lower()

def main():
    try:
        cur = json.load(open(PUB)).get("publications", [])
    except Exception:
        return
    try:
        prevtxt = subprocess.run(["git", "show", "HEAD:publications.json"], cwd=SITE,
                                 capture_output=True, timeout=25).stdout.decode("utf-8", "replace")
        prev = json.loads(prevtxt).get("publications", [])
    except Exception:
        prev = []
    if json.dumps(cur, sort_keys=True) == json.dumps(prev, sort_keys=True):
        print("pubs_activity: no change"); return
    prev_keys = {key(p) for p in prev}
    new = [p for p in cur if key(p) and key(p) not in prev_keys]
    now = datetime.datetime.now()
    when = now.strftime("%a %b %-d, %-I:%M %p")
    if new:
        titles = "; ".join((p.get("title") or "")[:80] for p in new)
        text = "🆕 Added %d new publication%s: %s" % (len(new), "" if len(new) == 1 else "s", titles)
    else:
        text = "🔄 Publications feed refreshed — links/authors updated (%d entries)" % len(cur)
    entry = {"ts": now.isoformat(timespec="seconds"), "when": when, "text": text, "count": len(new)}
    try:
        acts = json.load(open(ACT))
        if not isinstance(acts, list): acts = []
    except Exception:
        acts = []
    acts.insert(0, entry)
    try:
        json.dump(acts[:30], open(ACT, "w"), ensure_ascii=False, indent=2)
    except Exception as e:
        print("pubs_activity write failed: %r" % e); return
    print("pubs_activity:", text)

if __name__ == "__main__":
    try: main()
    except Exception as e: print("pubs_activity ERROR %r" % e)
