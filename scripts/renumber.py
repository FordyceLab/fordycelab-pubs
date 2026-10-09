#!/usr/bin/env python3
"""renumber.py [--no-push] - number every entry (oldest = 1, newest = total) and push.
publish.py does this automatically on every update; this is for running it by hand."""
import sys

from common import CFG, PUBS, ROOT, git_commit_push, load_pubs, renumber, save_json, today

if "--no-push" in sys.argv:
    CFG["git_push"] = False
pubs = renumber(load_pubs())
pubs["updated"] = today()
save_json(PUBS, pubs)
print("numbered %d entries; newest = #%d (%s)" % (pubs["count"], pubs["publications"][0]["num"], pubs["publications"][0]["title"][:50]))
print("git:", git_commit_push(ROOT, [PUBS], "Number entries 1..%d (oldest first)" % pubs["count"])[2])
