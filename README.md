# Fordyce Lab — auto-updating publications

Pulls all publications for **Polly M. Fordyce** from **OpenAlex** (free, no API
key), writes `publications.json`, and pushes it to GitHub. A small Code Block on
the Squarespace publications page reads that JSON and renders the list — grouped
by year, PI bolded, with **PDF / web / data** buttons and `preprint` tags.

The Mac mini never logs into Squarespace. It just maintains one JSON file.

```
Mac mini (weekly launchd job)
  update_publications.py  → OpenAlex → publications.json → git push
                                                                │
Squarespace page (Code Block, added once) ── fetches ──────────┘ → renders
```

## Files

| File | What it is |
|------|------------|
| `update_publications.py` | Fetches OpenAlex + merges overrides → `publications.json` |
| `overrides.json` | Your manual enrichments (hosted PDFs, data links, hide noise, equal-authorship marks) |
| `publications.json` | Generated output — do not hand-edit |
| `squarespace-code-block.html` | Paste-once snippet for the Squarespace page |
| `run_and_publish.sh` | Weekly runner: regenerate + git push if changed |
| `com.fordycelab.pubs.plist` | launchd schedule (Mondays 7 AM) |

## One-time setup (on the Mac mini)

These are the steps only you can do (they need your GitHub + Squarespace logins).

### 1. Put the folder on the Mac mini and make it a GitHub repo
Copy this folder to `~/lab-publications-site` (keep it **outside** Google Drive
to avoid sync conflicts with git). Then:

```bash
cd ~/lab-publications-site
git init && git add . && git commit -m "Initial publications setup"
gh repo create fordycelab-pubs --public --source=. --push   # needs gh, or use github.com
```

The public raw URL of your JSON will be:
```
https://raw.githubusercontent.com/<your-github-username>/fordycelab-pubs/main/publications.json
```

### 2. Wire that URL into the Squarespace snippet
Open `squarespace-code-block.html`, set `PUBLICATIONS_JSON_URL` to the raw URL
above. Then in Squarespace: **Edit the publications page → add a Code Block →
paste the whole file → Apply → Save.**

> Tip: a GitHub *Pages* URL (`https://<user>.github.io/fordycelab-pubs/publications.json`)
> works too and can be slightly faster; raw.githubusercontent.com is simplest.

### 3. Schedule the weekly refresh
```bash
chmod +x ~/lab-publications-site/run_and_publish.sh
cp ~/lab-publications-site/com.fordycelab.pubs.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fordycelab.pubs.plist
```
Test it once by hand: `~/lab-publications-site/run_and_publish.sh` then check
`last_run.log`.

## Everyday use: adding PDFs, data links, or hiding a paper

Edit `overrides.json`. Key each entry by its **DOI**. Fields (all optional):

```jsonc
"10.1126/science.abf8761": {
  "pdf": "https://www.fordycelab.com/s/yourpaper.pdf",   // hosted-PDF button
  "links_add": [                                          // extra buttons
    { "label": "GitHub",  "url": "https://github.com/FordyceLab/..." },
    { "label": "Zenodo",  "url": "https://zenodo.org/record/..." }
  ],
  "authors_html": "Markin, C.J.*, ... <strong>Fordyce, P.M.‡</strong>",  // * ‡ marks
  "note": "* equal contribution; ‡ co-corresponding",
  "hide": true,          // remove a same-name mismatch or dataset deposit
  "pin": true            // force to the very top
}
```

For a paper with **no DOI**, key it by its OpenAlex ID instead (e.g. `"W3196281287"`).
The weekly job re-applies overrides automatically; to see changes immediately,
run `python3 update_publications.py` yourself.

## Notes
- OpenAlex occasionally attaches a same-name stranger's paper or a dataset
  deposit. A few are pre-hidden in `overrides.json`; add `"hide": true` for any
  others you spot.
- Author names are auto-formatted `Surname, F.M.` with **you** bolded. To bold
  or mark other lab members on a specific paper, set `authors_html` for it.
- Data source can be swapped/extended (PubMed, Crossref) later without touching
  the Squarespace side — the page only ever reads `publications.json`.
