#!/bin/zsh
# Biweekly paper sync (Mac mini, launchd com.fordycelab.papers, Mondays 07:00).
# Self-gates to every other week: skips if the last full run was < 13 days ago.
# Safe to run by hand any time:  ~/fordycelab-pubs/scripts/run_biweekly.sh [--force]
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
P=/usr/bin/python3
R="$HOME/fordycelab-pubs"
mkdir -p "$R/logs"
STAMP="$R/logs/last_full_run"
if [[ "$1" != "--force" && -f "$STAMP" ]]; then
  last=$(cat "$STAMP"); now=$(date +%s)
  if (( now - last < 13*86400 )); then
    echo "[$(date)] skipped: last full run $(( (now-last)/86400 )) days ago" >> "$R/logs/run.log"
    exit 0
  fi
fi
cd "$R" || exit 1
git pull -q --rebase 2>>"$R/logs/run.log" || true
"$P" scripts/poll.py            >> "$R/logs/run.log" 2>&1
"$P" scripts/publish.py --sweep >> "$R/logs/run.log" 2>&1
"$P" scripts/notify.py --poll   >> "$R/logs/run.log" 2>&1
date +%s > "$STAMP"
