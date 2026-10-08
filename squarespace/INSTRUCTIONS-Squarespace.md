# One-time Squarespace change for the Fordyce Lab site: the Data page

The Publications page is already done (it reads `publications.json` from GitHub). One edit remains:
the Data page counters are hard-coded numbers; this swap makes them read `stats.json` from GitHub so
they update whenever data is added. About 5 minutes.

The file to paste is in the GitHub repo `FordyceLab/fordycelab-pubs`, folder `squarespace/`:
`data-page-code-block.html` (open it on GitHub, click "Raw", select all, copy).

1. Log in to Squarespace → **Pages** → **Data** (URL `/data`) → **Edit**.
2. The page is a single **Code** block (counters, donut chart, technology cards). Hover it and click the pencil/edit icon.
3. Select everything inside the editor (Cmd-A) and replace it with the entire contents of `data-page-code-block.html`. Click **Apply**, then **Save**.
4. View the live page logged out (Squarespace disables embedded scripts while you are logged in, so the block looks blank in the editor; that is expected). The counters should show the same numbers as before.
5. If anything looks wrong, use Squarespace's version history to restore, or paste back
   `squarespace/data-page-code-block.ORIGINAL.html` from the same folder.

Afterwards nothing else is needed; the mini stops reminding once it sees the new block.
