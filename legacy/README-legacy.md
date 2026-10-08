# fordycelab-pubs

Source of truth for the publications list on fordycelab.com/publications-v2 (the page's Code
Block fetches `publications.json` from this repo), plus the biweekly job that finds new papers,
asks Polly on the Cy dashboard, and updates the website and the Data Portal.

```
Mac mini, every other Monday 07:00 (launchd com.fordycelab.papers)
  scripts/poll.py        OpenAlex + PubMed + arXiv + Scholar-alert emails -> pending.json
  dashboard card         Polly answers: website? data portal? repo URL; upload PDFs; add a paper manually
  scripts/publish.py     entry + hosted PDFs + link check -> publications.json -> git push
  scripts/ingest_propose.py   repo download + Claude draft   -> ingest/<id>/proposal.csv
  scripts/portal_merge.py     approved rows -> ../data-portal/data.json + stats.json -> git push
  squarespace-code-block.html   the Code Block already on the publications page (reads publications.json)
  squarespace/data-page-code-block.html   one-time Code Block for the Data page (still to paste)
```

- PDFs live in `pdfs/` and are linked as `https://raw.githubusercontent.com/FordyceLab/fordycelab-pubs/main/pdfs/<file>`.
  To host your own copy of a paper: upload it on the dashboard card, or add the file to `pdfs/` on
  GitHub with the DOI suffix in the filename (e.g. `science.add1250.pdf`); the next sweep attaches it.
- Nothing is published without a yes on the dashboard; nothing is ever deleted by the job.
- `legacy/` holds the previous OpenAlex-only pipeline (retired 2026-10-08).
