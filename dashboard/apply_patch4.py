#!/usr/bin/env python3
"""Patch 4 (2026-10-08): re-wire the papers card after another session's Sept-22 dashboard rewrite
replaced it with a "Publications feed" notification card of the same name.
  - app.py:  build_data gains d["papers"] = papers_data() again
  - app.js:  their renderPapers(list) -> renderPubsFeed(list) on #pubsFeed; our renderPapers(pd)
             (approve-first card, always rendered, with "➕ add a paper") is restored and called in load()
  - app.html: <div id="pubsFeed"> added next to <div id="papers">
Idempotent + backups.
"""
import datetime
import os
import re
import shutil

SRV = os.path.expanduser("~/cy/server")
TS = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def patch(fn, marker, transform):
    p = os.path.join(SRV, fn)
    s = open(p).read()
    if marker in s:
        print(fn, "already patched (4)")
        return
    shutil.copy(p, p + ".bak-papers4-" + TS)
    s2 = transform(s)
    if s2 == s:
        raise SystemExit("patch4 for %s did not apply (anchor not found)" % fn)
    open(p, "w").write(s2)
    print(fn, "patched (4)")


RENDER = r'''
  // ---------- papers card: approve-first (fordycelab-pubs, restored 2026-10-08) ----------
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
  function renderPapers(pd) {
    var host = document.getElementById("papers"); if (!host) return; host.innerHTML = "";
    pd = pd || {}; var list = pd.items || []; var flags = pd.flags || {};
    var needSq = !flags.pubs_switched || !flags.data_switched;
    var card = el("div", "card papercard");
    var head = el("div", "card-head"); var h = el("h2"); h.appendChild(el("span", "tick"));
    h.appendChild(document.createTextNode(" 📚 Papers — website / data portal")); head.appendChild(h);
    var right = el("div", "hd-right");
    var addb = el("button", "tune", "➕ add a paper"); addb.title = "add something not indexed anywhere (a review in preparation, a chapter)";
    addb.addEventListener("click", addPaperManually);
    var chk = el("button", "tune", "↻ check now"); chk.title = "run the PubMed / OpenAlex / arXiv / Scholar check now";
    chk.addEventListener("click", function () { act({ type: "paper_poll" }); toast("checking… (2–3 min)"); setTimeout(load, 150000); });
    right.appendChild(addb); right.appendChild(chk); right.appendChild(el("span", "count", String(list.length)));
    head.appendChild(right); card.appendChild(head);
    var body = el("div", "card-body");
    if (needSq) {
      var n = el("div", "paper-sq");
      n.innerHTML = "⚠️ One-time Squarespace step still pending for the" + (!flags.pubs_switched ? " <b>Publications page</b>" : "") +
        (!flags.pubs_switched && !flags.data_switched ? " and" : "") + (!flags.data_switched ? " <b>Data page</b>" : "") +
        ". <a href='/squarespace-instructions' target='_blank'>Instructions (written so Ana can do it) ↗</a>";
      body.appendChild(n);
    }
    if (!list.length) body.appendChild(el("div", "paper-empty", "Nothing waiting for you. Last check: " + (pd.last_poll || "—") + " · use ➕ add a paper for something not indexed anywhere."));
    list.forEach(function (it) { body.appendChild(paperRow(it)); });
    card.appendChild(body); host.appendChild(card);
  }
'''


def main():
    patch("app.py", 'd["papers"] = papers_data()', lambda s: s.replace(
        '    d["pubs_activity"] = load_json(PUBSACT, [])[:20]\n',
        '    d["pubs_activity"] = load_json(PUBSACT, [])[:20]\n    d["papers"] = papers_data()\n', 1))

    def js(s):
        s = s.replace("  function renderPapers(list) {\n    var host = document.getElementById(\"papers\");",
                      "  function renderPubsFeed(list) {\n    var host = document.getElementById(\"pubsFeed\");", 1)
        s = s.replace("      renderPapers(d.pubs_activity);\n", "      renderPubsFeed(d.pubs_activity);\n      renderPapers(d.papers);\n", 1)
        if "function addPaperManually" in s:
            s = re.sub(r"\n  // papers card: manual add \(fordycelab-pubs\)\n  function addPaperManually\(\) \{.*?\n  \}\n", "\n", s, count=1, flags=re.S)
        s = s.replace("  function load() {", RENDER + "  function load() {", 1)
        s = s.replace('var edit = el("button", "tune", "✎ edit entry text");', 'var edit = el("button", "tune", "✎ edit author line");', 1)
        return s
    patch("app.js", "approve-first (fordycelab-pubs, restored 2026-10-08)", js)
    patch("app.html", 'id="pubsFeed"', lambda s: s.replace('  <div id="papers"></div>', '  <div id="papers"></div>\n  <div id="pubsFeed"></div>', 1))
    import py_compile
    py_compile.compile(os.path.join(SRV, "app.py"), doraise=True)
    s = open(os.path.join(SRV, "app.js")).read()
    for needle in ("function renderPapers(pd)", "function renderPubsFeed(list)", "renderPapers(d.papers)", "function paperRow", "function pdfUploadBtn", "function toggleGroup", "function addPaperManually"):
        print("  %-32s %s" % (needle, "ok" if needle in s else "MISSING"))
    print("ok - now restart:  launchctl kickstart -k gui/$(id -u)/com.cy.server")


if __name__ == "__main__":
    main()
