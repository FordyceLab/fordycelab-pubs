# Plan: biweekly new-paper sync (website + data portal)

Drafted 2026-09-14. **Status 2026-09-14 evening: BUILT and RUNNING on the Mac mini.**
Code lives on the mini at `~/fordycelab-pubs` (to become GitHub repo FordyceLab/fordycelab-pubs).
Pending on Polly: create the GitHub repo + add the two deploy keys (see "Things only Polly can provide"),
then the two Squarespace pastes (`squarespace/INSTRUCTIONS-Squarespace.md`, also at
http://pollys-mac-mini:8787/squarespace-instructions). Drop PDFs the job cannot fetch into `pdf-inbox/`
in this folder using the filename shown on the dashboard card.

## What exists today (verified 2026-09-14)

- **Mac mini** (`ssh pollyfordyce@pollys-mac-mini`, Tailscale): reachable. Runs the Cy jobs
  under launchd (`~/cy/…`, `com.cy.refresh` hourly). Claude Code 2.1.114 at
  `/opt/homebrew/bin/claude`; headless `claude -p` WORKS with the API key at
  `~/.config/anthropic/key` (key file was rewritten 2026-09-14). No `gh`, no `requests`,
  no git push credentials yet. Both Google Drives sync to the mini, so this folder is
  already there at `~/pfordyce@stanford.edu - Google Drive/My Drive/claude/lab-publications-site`.
- **Publications page** (fordycelab.com/publications-v2): a hand-edited Squarespace
  rich-text block. Entry format: `Authors (Fordyce underlined+bold, ‡ marks) "Title", *Venue*
  (Year). (pdf) (web) (data) (‡ = co-corresponding)`. PDFs are Squarespace-hosted at `/s/…`.
  Preprint and published versions currently appear as SEPARATE entries.
- **Auto-updating pubs system** (this folder): built 2026-08-10, never deployed. OpenAlex →
  `publications.json` → GitHub → Squarespace Code Block renders it. Needs adapting so the
  rendered output matches the current page format exactly.
- **Data portal**: Squarespace `/data` page with HARD-CODED counters (donut categories,
  technology cards, "4.2M+"), and `/data-measurements` which fetches
  `https://fordycelab.github.io/data-portal/data.json` (GitHub repo FordyceLab/data-portal,
  23,765 rows, 12-field schema: p, y, pw, pm, lib, lig, mt, v, u, lim, pv, m).
  The 4.2M headline = data.json rows + the two BET-seq CSVs (2.1M each) which are NOT in data.json.
- **Sources that work from a script**: PubMed E-utilities (`Fordyce PM[Author]`), OpenAlex
  (author A5085863617, includes bioRxiv), bioRxiv API (gives preprint→published DOI),
  Crossref. Google Scholar has no API and blocks scrapers; Scholar alert emails DO land in
  Gmail (scholaralerts-noreply@google.com) and the mini already reads Gmail, so they are a
  secondary signal.
- **Gap found today**: two papers were published in July 2026 but the site still shows only
  their bioRxiv versions: ADAPT-M (Nature Communications, 10.1038/s41467-026-75463-1) and
  SPARKfold unfolding kinetics (Cell Systems, 10.1016/j.cels.2026.101681). These would be the
  first run's work.

## Architecture

```
Mac mini, launchd, every other Monday 07:00
  1. poll.py        PubMed + OpenAlex + bioRxiv + Crossref (+ Scholar-alert emails)
                    → diff against state.json → list of NEW works
                    → link preprint↔published (bioRxiv "published" field, Crossref, DOI/title match)
  2. ask            for each new work: write a review card (dashboard + email nudge)
                    with two toggles: [website? y/n] [data portal? y/n] + "which repo URL?"
  3. wait           nothing changes until Polly answers (answers persist; re-asked next run if silent)
  4. publish.py     approved → fetch PDFs, build/merge the entry, regenerate publications.json,
                    git push → Squarespace Code Block re-renders (no Squarespace login)
  5. ingest         approved for portal → download repo → headless Claude proposes a mapping
                    to the 12-field schema → Polly approves the proposal → merge into data.json,
                    regenerate stats.json (counters) → git push data-portal
  6. report         one email: what was added, what is waiting on you, anything that failed
```

Judgment steps (dedupe, author formatting, repo mapping) run via `claude -p` with the API
key; polling, diffing, git, and JSON are plain Python.

## Step details

### 1. Detection
- State file `state.json` records every DOI/OpenAlex ID/PMID already seen.
- A work is NEW if unseen in any source. Preprint→published links come from bioRxiv API
  (`published` field), Crossref `relation.is-preprint-of`, OpenAlex, and a title-similarity
  fallback; ambiguous links are flagged for Polly rather than guessed.
- Same-name-stranger papers: default filter = Polly is an author per OpenAlex author ID; the
  review card is the safety net.

### 2. Asking Polly
Recommended: a "📚 New papers" card on the Cy dashboard (http://pollys-mac-mini:8787) with
per-paper toggles and a repo-URL box, plus a short email to fordyce@gmail.com listing the
papers and linking to the card. Fallback: reply to the email ("1: web yes, data no").
Both feed the same `decisions.json`.

### 3. Website entry
- ONE entry per paper. Venue = journal once published; buttons: `pdf` (published PDF),
  `web` (journal), `bioRxiv pdf`, `bioRxiv` (web), plus one button per data repo
  (`OSF data`, `Zenodo`, `GitHub`), plus `(‡ = co-corresponding)` note when present.
- PDFs: bioRxiv PDF auto-downloaded. Published PDF auto-downloaded when open access
  (Unpaywall/OpenAlex `best_oa_location`); otherwise Polly drops the file in
  `lab-publications-site/pdf-inbox/` (Drive → mini) and the next run picks it up.
  PDFs hosted from the same GitHub repo via GitHub Pages (Squarespace uploads need a login).
- Link check: every button is HEAD-requested before publishing; failures go in the report.
- Author formatting: `Surname, F.M.`, Fordyce underlined+bold, ‡/* marks carried from the
  preprint entry or from the review card.
- Rendering: adapt `squarespace-code-block.html` to reproduce the current page look
  (year headings, parenthesised links, note). One-time migration: parse the current
  page HTML into `publications.json` + `overrides.json` so the switch is visually a no-op.

### 4. Data portal
- The portal schema is per-measurement rows keyed by study (`p`, e.g. `Lee2026_bioRxiv_SHP2`).
  Every paper's deposited data is formatted differently, so ingestion is
  propose-then-approve: the job downloads the repo, Claude inspects the files, writes a
  candidate CSV in the 12-field schema plus a summary (rows, measurement types, protein,
  technology, units, any columns it could not map), and Polly approves or corrects it.
- On approval: rows appended to data.json (`p` key = `<FirstAuthor><Year>_<Venue>`), and
  when a preprint becomes published the existing `p` key is renamed (e.g. `_bioRxiv` →
  `_NatComm`) so the portal stays consistent with the website.
- Counters: new `stats.json` generated from data.json + BET-seq constants (total
  measurements, studies, protein systems, technologies, per-type counts, per-technology
  counts). One-time Squarespace edit replaces the hard-coded numbers in the `/data`
  Code Block with a fetch of stats.json; after that the counters update themselves.

### 5. Reporting and safety
- Every run appends to `runs.log` and emails a summary only when something happened.
- Nothing is published without an explicit yes per paper; nothing is deleted, ever.
- Preview: publications.json is rendered to a local HTML preview attached to the review
  email so Polly sees the exact entry before it goes live.

## Two Squarespace logins, once each (Polly present, Cy drives Chrome)
1. Publications page: replace the rich-text block with the Code Block pointing at
   publications.json (after the migration preview matches the current page).
2. Data page: swap hard-coded counters for the stats.json fetch.
After these, the mini never touches Squarespace.

## Things only Polly can provide
- GitHub: push access from the mini (a fine-grained token or deploy key for
  `FordyceLab/data-portal` and the new pubs repo, and confirm which org/user hosts the pubs repo).
- Anthropic API key expiry date for the mini (file rewritten 2026-09-14; the dashboard
  reminder needs the new date).
- Decisions listed in the chat summary (asking channel, published-PDF hosting policy,
  cadence day/time, whether to track only Polly-authored works).

## Build order
1. poll + dedupe + state (dry run against the current page; expect the 2 July papers).
2. review card + email + decisions file.
3. pubs renderer matching current format + migration + preview.
4. PDF fetch + hosting + link check + git push.
5. stats.json + data.json merge tool + propose/approve ingestion.
6. launchd biweekly job + first supervised run + the two Squarespace switches.
