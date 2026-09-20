#!/bin/bash
#
# run_and_publish.sh
# ------------------
# Weekly job: refresh publications.json from OpenAlex and, if it changed,
# push it to GitHub so the Squarespace page picks up the update.
#
# Run by launchd (see com.fordycelab.pubs.plist). Safe to run by hand too.

set -euo pipefail

# --- EDIT: absolute path to this repo on the Mac mini ---------------------
REPO_DIR="$HOME/lab-publications-site"
# -------------------------------------------------------------------------

cd "$REPO_DIR"

LOG="$REPO_DIR/last_run.log"
echo "===== $(date) =====" >> "$LOG"

# 1. Regenerate publications.json
/usr/bin/python3 update_publications.py >> "$LOG" 2>&1

# 1b. Email Polly if the update added a new paper (never breaks the publish)
/usr/bin/python3 notify_new.py >> "$LOG" 2>&1 || true

# 2. Commit + push only if something actually changed
if [[ -n "$(git status --porcelain publications.json)" ]]; then
  git pull --rebase >> "" 2>&1 || true
  git add publications.json
  git commit -m "Auto-update publications ($(date +%Y-%m-%d))" >> "$LOG" 2>&1
  git push >> "$LOG" 2>&1
  echo "Pushed updated publications.json" >> "$LOG"
else
  echo "No change; nothing to push" >> "$LOG"
fi
