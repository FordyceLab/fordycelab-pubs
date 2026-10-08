#!/usr/bin/env python3
"""Patch 3 for the Cy dashboard papers card:
  - "➕ add a paper" (manual entry: unpublished review, book chapter...) -> POST /api/paper_add
  - card always renders (so the add button is reachable), "edit author line" wording
Idempotent (markers) + backups. Run on the mini, then restart com.cy.server.
"""
import datetime
import os
import shutil

SRV = os.path.expanduser("~/cy/server")
TS = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def patch(fn, marker, transform):
    p = os.path.join(SRV, fn)
    s = open(p).read()
    if marker in s:
        print(fn, "already patched (3)")
        return
    shutil.copy(p, p + ".bak-papers3-" + TS)
    s2 = transform(s)
    if s2 == s:
        raise SystemExit("patch3 for %s did not apply (anchor not found)" % fn)
    open(p, "w").write(s2)
    print(fn, "patched (3)")


APP_PY_ADD = '''
def paper_add(a):   # papers card: manual entry (fordycelab-pubs)
    import re as _re
    title = (a.get("title") or "").strip()
    if not title: raise ValueError("title is required")
    sl = _re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:50]
    pid = "manual:" + sl
    authors = [{"name": n.strip(), "corresponding": False} for n in _re.split(r"[;,]\\s*(?=[A-Z])|;", a.get("authors") or "") if n.strip()]
    fa = authors[0]["name"].split()[-1] if authors else "paper"
    try: year = int(str(a.get("year") or "").strip()[:4])
    except Exception: year = datetime.date.today().year
    it = {"id": pid, "doi": "", "type": "manual", "title": title, "year": year, "venue": (a.get("venue") or "").strip(),
          "first_author": fa, "authors": authors, "web_url": (a.get("url") or "").strip(), "note": (a.get("note") or "").strip(),
          "pdf_url": "", "pdf_candidates": [], "preprint": None, "data_links": [], "sources": ["added by Polly"], "verified": True,
          "status": "added manually - waiting for the PDF", "seen_first": datetime.date.today().isoformat()}
    import fcntl
    os.makedirs(os.path.join(PUBSDIR, "logs"), exist_ok=True)
    with open(os.path.join(PUBSDIR, "logs", "pending.json.lock"), "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        items = load_json(PUBS_PENDING, [])
        items = [x for x in items if x["id"] != pid] + [it]
        save_json(PUBS_PENDING, items)
    d = load_json(PUBS_DECISIONS, {})
    d[pid] = {"website": True, "portal": False, "repo_url": "", "ts": datetime.datetime.now().isoformat()}
    save_json(PUBS_DECISIONS, d)
    _pubs_run("publish.py", "--id", pid)
    return pid

'''

APP_JS_ADD = r'''
  // papers card: manual add (fordycelab-pubs)
  function addPaperManually() {
    var title = window.prompt("Title of the paper / review:"); if (!title) return;
    var authors = window.prompt("Authors, full names separated by commas (e.g. Polly M. Fordyce, Daniel Herschlag):", "Polly M. Fordyce") || "";
    var venue = window.prompt("Where it appears (e.g. 'In preparation', 'Submitted', 'Annual Review of Biophysics'):", "In preparation") || "";
    var year = window.prompt("Year:", String(new Date().getFullYear())) || "";
    var url = window.prompt("Web link (optional - leave blank if none):", "") || "";
    fetch("/api/action", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "paper_add", title: title, authors: authors, venue: venue, year: year, url: url }) })
      .then(function (r) { return r.json(); })
      .then(function (j) { if (!j.ok) throw new Error(j.error || "failed"); toast("added - now upload its PDF on the card"); setTimeout(load, 4000); })
      .catch(function (e) { toast(String(e.message || e), true); });
  }
'''


def main():
    patch("app.py", "papers card: manual entry (fordycelab-pubs)", lambda s: (
        s.replace("def paper_portal(a):", APP_PY_ADD + "def paper_portal(a):", 1)
         .replace('    elif t == "paper_poll":     _pubs_run("poll.py")\n',
                  '    elif t == "paper_poll":     _pubs_run("poll.py")\n    elif t == "paper_add":      paper_add(a)\n', 1)))
    patch("app.js", "papers card: manual add (fordycelab-pubs)", lambda s: (
        s.replace("  function renderPapers(pd) {", APP_JS_ADD + "  function renderPapers(pd) {", 1)
         .replace("    if (!list.length && !needSq) return;\n", "", 1)
         .replace('    right.appendChild(chk); right.appendChild(el("span", "count", String(list.length)));',
                  '    var addb = el("button", "tune", "➕ add a paper"); addb.title = "add something not indexed anywhere (a review in preparation, a chapter)";\n'
                  '    addb.addEventListener("click", addPaperManually);\n'
                  '    right.appendChild(addb); right.appendChild(chk); right.appendChild(el("span", "count", String(list.length)));', 1)
         .replace('var edit = el("button", "tune", "✎ edit entry text");', 'var edit = el("button", "tune", "✎ edit author line");', 1)
         .replace('var cur = override || ((it.authors_html || "") + " “" + (it.title || "") + "”, <em>" + (it.venue || "") + "</em> (" + (it.year || "") + ").");',
                  'var cur = override || (it.authors_html || "");', 1)
         .replace('var v = window.prompt("Entry HTML as it will appear on the site. Link buttons (pdf/web/bioRxiv/data) are appended automatically after this text:", cur);',
                  'var v = window.prompt("Author line as it will appear on the site (HTML ok: <strong>Fordyce, P.M.</strong>, * and ‡ marks):", cur);', 1)
         .replace('if (!list.length) body.appendChild(el("div", "paper-empty", "Nothing waiting for you. Last check: " + (pd.last_poll || "—")));',
                  'if (!list.length) body.appendChild(el("div", "paper-empty", "Nothing waiting for you. Last check: " + (pd.last_poll || "—") + " · use ➕ add a paper for something not indexed anywhere."));', 1)))
    import py_compile
    py_compile.compile(os.path.join(SRV, "app.py"), doraise=True)
    print("ok - now restart:  launchctl kickstart -k gui/$(id -u)/com.cy.server")


if __name__ == "__main__":
    main()
