#!/usr/bin/env python3
"""Add the "📚 New papers" card to the Cy dashboard on the Mac mini (~/cy/server).
Idempotent: each file is patched once (marker strings) and backed up first.
Run on the mini:  python3 ~/fordycelab-pubs/dashboard/apply_patch.py && launchctl kickstart -k gui/$(id -u)/com.cy.server
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
        print(fn, "already patched")
        return
    shutil.copy(p, p + ".bak-papers-" + TS)
    s2 = transform(s)
    if s2 == s:
        raise SystemExit("patch for %s did not apply (anchor not found)" % fn)
    open(p, "w").write(s2)
    print(fn, "patched (backup: %s)" % (p + ".bak-papers-" + TS))


APP_PY_BLOCK = '''
# --- papers card (fordycelab-pubs) -------------------------------------------------
PUBSDIR = os.path.join(HOME, "fordycelab-pubs")
PUBS_PENDING = os.path.join(PUBSDIR, "pending.json")
PUBS_DECISIONS = os.path.join(PUBSDIR, "decisions.json")
PUBS_STATE = os.path.join(PUBSDIR, "state.json")

def papers_data():
    items = load_json(PUBS_PENDING, [])
    dec = load_json(PUBS_DECISIONS, {})
    for it in items:
        it["decision"] = dec.get(it["id"], {})
    st = load_json(PUBS_STATE, {})
    return {"items": items, "flags": st.get("flags", {}), "last_poll": st.get("last_poll", "")}

def _pubs_run(script, *args):
    try:
        os.makedirs(os.path.join(PUBSDIR, "logs"), exist_ok=True)
        subprocess.Popen(["/usr/bin/python3", os.path.join(PUBSDIR, "scripts", script)] + list(args),
                         stdout=open(os.path.join(PUBSDIR, "logs", "server-spawn.log"), "a"),
                         stderr=subprocess.STDOUT, start_new_session=True, cwd=PUBSDIR)
    except Exception: pass

def paper_decide(a):
    d = load_json(PUBS_DECISIONS, {})
    cur = d.get(a["id"], {})
    cur.update({"website": a.get("website"), "portal": a.get("portal"), "repo_url": a.get("repo_url", ""),
                "html_override": a.get("html_override") or cur.get("html_override"),
                "ts": datetime.datetime.now().isoformat()})
    d[a["id"]] = cur
    save_json(PUBS_DECISIONS, d)
    _pubs_run("publish.py", "--id", a["id"])

def paper_portal(a):
    args = [a["id"]] + ([] if a.get("approve") else ["--reject"])
    if a.get("rename_from") and a.get("rename_to"):
        args += ["--rename", a["rename_from"], a["rename_to"]]
    _pubs_run("portal_merge.py", *args)
# -----------------------------------------------------------------------------------

'''

APP_PY_ACTIONS = '''    elif t == "paper_decision": paper_decide(a)
    elif t == "paper_portal":   paper_portal(a)
    elif t == "paper_poll":     _pubs_run("poll.py")
'''

APP_JS_BLOCK = r'''
  // ---------- papers card (fordycelab-pubs) ----------
  function toggleGroup(label) {
    var box = el("span", "paper-tg"); box.appendChild(el("span", "paper-tg-label", label + ":"));
    var val = null; var y = el("button", "paper-tg-btn", "yes"), n = el("button", "paper-tg-btn", "no");
    function paint() { y.classList.toggle("on", val === true); n.classList.toggle("on", val === false); }
    y.addEventListener("click", function () { val = true; paint(); });
    n.addEventListener("click", function () { val = false; paint(); });
    box.appendChild(y); box.appendChild(n); paint();
    return { box: box, value: function () { return val; } };
  }
  function paperRow(it) {
    var dec = it.decision || {};
    var row = el("div", "paper-row");
    var top = el("div", "paper-top");
    top.appendChild(el("span", "paper-tag " + (it.type === "preprint" ? "pre" : "art"),
      it.type === "preprint" ? "preprint" : (it.type === "article" ? "published" : "unknown")));
    var a = el("a", "paper-title", it.title || "(untitled)"); a.href = it.web_url || "#"; a.target = "_blank"; a.rel = "noopener";
    top.appendChild(a); row.appendChild(top);
    row.appendChild(el("div", "paper-meta", (it.first_author ? it.first_author + " et al. · " : "") + (it.venue || "") +
      (it.year ? " (" + it.year + ")" : "") + " · found via " + (it.sources || []).join(", ") +
      (it.verified ? "" : " · ⚠️ author match not verified — please check it is really yours")));
    if (it.merge_note) row.appendChild(el("div", "paper-note", "🔗 " + it.merge_note));
    var found = [];
    if (it.pdf_url) found.push("published PDF: open access ✓"); else if (it.type === "article") found.push("no open-access PDF found — I'll ask you for it after you approve");
    if (it.preprint && (it.preprint.doi || it.preprint.web)) found.push("preprint: " + (it.preprint.venue || "bioRxiv") + " ✓");
    (it.data_links || []).forEach(function (d) { found.push(d.label + ": " + d.url); });
    if (it.portal_existing_keys && it.portal_existing_keys.length) found.push("already in the Data Portal as " + it.portal_existing_keys.join(", "));
    if (found.length) row.appendChild(el("div", "paper-found", found.join(" · ")));
    var decided = dec.website === true || dec.website === false;
    if (!decided) {
      var ctl = el("div", "paper-ctl");
      var web = toggleGroup("Website"), por = toggleGroup("Data portal");
      ctl.appendChild(web.box); ctl.appendChild(por.box);
      var repo = el("input", "paper-repo"); repo.type = "text";
      repo.placeholder = "data repository URL(s) — OSF / Zenodo / GitHub (needed for Data portal = yes)";
      repo.value = (it.data_links || []).map(function (d) { return d.url; }).join(", ");
      ctl.appendChild(repo);
      var override = null;
      var edit = el("button", "tune", "✎ edit entry text");
      edit.addEventListener("click", function () {
        var cur = override || ((it.authors_html || "") + " “" + (it.title || "") + "”, <em>" + (it.venue || "") + "</em> (" + (it.year || "") + ").");
        var v = window.prompt("Entry HTML as it will appear on the site. Link buttons (pdf/web/bioRxiv/data) are appended automatically after this text:", cur);
        if (v) { override = v; edit.textContent = "✎ edited"; }
      });
      var save = el("button", "btn btn-primary", "Save decision"); save.style.fontSize = "12px"; save.style.padding = "6px 12px";
      save.addEventListener("click", function () {
        if (web.value() === null) { toast("choose Website yes/no first", true); return; }
        if (por.value() === true && !repo.value.trim()) { toast("Data portal = yes needs a repository URL", true); return; }
        act({ type: "paper_decision", id: it.id, website: web.value(), portal: por.value(), repo_url: repo.value.trim(), html_override: override });
        row.style.opacity = "0.55"; toast("saved — Cy is on it"); setTimeout(load, 9000);
      });
      ctl.appendChild(edit); ctl.appendChild(save); row.appendChild(ctl);
    } else {
      row.appendChild(el("div", "paper-status", "You said: website " + (dec.website ? "yes" : "no") + ", data portal " +
        (dec.portal === true ? "yes" : dec.portal === false ? "no" : "—") + (dec.repo_url ? " (" + dec.repo_url + ")" : "") + " · " + (it.status || "")));
      if (it.pdf_status) row.appendChild(el("div", "paper-warn", "📄 " + it.pdf_status));
      if (it.preprint_pdf_status) row.appendChild(el("div", "paper-warn", "📄 " + it.preprint_pdf_status));
      if (it.website_links) {
        var bad = it.website_links.filter(function (l) { return !l.ok; });
        if (bad.length) row.appendChild(el("div", "paper-warn", "🔗 broken: " + bad.map(function (l) { return l.label + " (" + l.note + ")"; }).join(", ")));
      }
      if (it.portal_proposal) {
        var pp = it.portal_proposal; var box = el("div", "paper-proposal");
        box.appendChild(el("div", "paper-pp-head", "📊 Data Portal proposal: " + pp.rows + " rows · study key " + pp.p + " · types: " + (pp.types || []).join(", ")));
        box.appendChild(el("pre", "paper-pp-sum", pp.summary || ""));
        var btns = el("div", "paper-ctl");
        var ok = el("button", "btn btn-primary", "✓ Merge into Data Portal"); ok.style.fontSize = "12px"; ok.style.padding = "6px 12px";
        var no = el("button", "tune", "✗ don't add");
        var renameFrom = (it.portal_existing_keys || []).filter(function (k) { return k !== pp.p; })[0];
        var ren = null;
        if (renameFrom && pp.p) {
          ren = el("label", "paper-rename"); var cb = el("input"); cb.type = "checkbox"; cb.checked = true;
          ren.appendChild(cb); ren.appendChild(document.createTextNode(" also rename existing rows " + renameFrom + " → " + pp.p)); ren._cb = cb;
        }
        ok.addEventListener("click", function () {
          var p = { type: "paper_portal", id: it.id, approve: true };
          if (ren && ren._cb.checked) { p.rename_from = renameFrom; p.rename_to = pp.p; }
          act(p); box.style.opacity = "0.55"; toast("merging + regenerating counters…"); setTimeout(load, 20000);
        });
        no.addEventListener("click", function () { act({ type: "paper_portal", id: it.id, approve: false }); box.style.opacity = "0.55"; setTimeout(load, 6000); });
        btns.appendChild(ok); btns.appendChild(no); if (ren) btns.appendChild(ren); box.appendChild(btns); row.appendChild(box);
      }
    }
    return row;
  }
  function renderPapers(pd) {
    var host = document.getElementById("papers"); if (!host) return; host.innerHTML = "";
    pd = pd || {}; var list = pd.items || []; var flags = pd.flags || {};
    var needSq = !flags.pubs_switched || !flags.data_switched;
    if (!list.length && !needSq) return;
    var card = el("div", "card papercard");
    var head = el("div", "card-head"); var h = el("h2"); h.appendChild(el("span", "tick"));
    h.appendChild(document.createTextNode(" 📚 New papers — website / data portal?")); head.appendChild(h);
    var right = el("div", "hd-right");
    var chk = el("button", "tune", "↻ check now"); chk.title = "run the PubMed / OpenAlex / arXiv / Scholar check now";
    chk.addEventListener("click", function () { act({ type: "paper_poll" }); toast("checking… (2–3 min)"); setTimeout(load, 150000); });
    right.appendChild(chk); right.appendChild(el("span", "count", String(list.length)));
    head.appendChild(right); card.appendChild(head);
    var body = el("div", "card-body");
    if (needSq) {
      var n = el("div", "paper-sq");
      n.innerHTML = "⚠️ One-time Squarespace step still pending for the" + (!flags.pubs_switched ? " <b>Publications page</b>" : "") +
        (!flags.pubs_switched && !flags.data_switched ? " and" : "") + (!flags.data_switched ? " <b>Data page</b>" : "") +
        ". Until it is done, website changes are staged on GitHub but not live. <a href='/squarespace-instructions' target='_blank'>Instructions (written so Ana can do it) ↗</a>";
      body.appendChild(n);
    }
    if (!list.length) body.appendChild(el("div", "paper-empty", "Nothing waiting for you. Last check: " + (pd.last_poll || "—")));
    list.forEach(function (it) { body.appendChild(paperRow(it)); });
    card.appendChild(body); host.appendChild(card);
  }

'''

CSS_BLOCK = '''
/* --- papers card (fordycelab-pubs) --- */
.papercard { border: 1px solid color-mix(in srgb, #2563eb 34%, var(--border)); border-left: 3px solid #2563eb; margin-bottom: 20px; }
.papercard .card-head h2 { color: #2563eb; }
.paper-sq { background: color-mix(in srgb, #f0a020 14%, transparent); border: 1px solid color-mix(in srgb, #f0a020 40%, transparent); border-radius: 8px; padding: 8px 12px; font-size: 13px; margin-bottom: 10px; }
.paper-sq a { color: var(--cyan); }
.paper-empty { font-size: 13px; color: var(--ink-faint); }
.paper-row { padding: 12px 0; border-bottom: 1px solid var(--border); transition: opacity .2s; }
.paper-row:last-child { border-bottom: 0; }
.paper-top { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.paper-tag { font-size: 10.5px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; border-radius: 5px; padding: 1px 7px; }
.paper-tag.pre { color: #b26a00; background: color-mix(in srgb, #f0a020 16%, transparent); }
.paper-tag.art { color: #1d4ed8; background: color-mix(in srgb, #2563eb 14%, transparent); }
.paper-title { font-size: 14.5px; font-weight: 600; color: var(--ink); text-decoration: none; }
.paper-title:hover { text-decoration: underline; }
.paper-meta, .paper-found { font-size: 12px; color: var(--ink-soft); margin-top: 3px; word-break: break-word; }
.paper-note { font-size: 12.5px; color: var(--ink); margin-top: 4px; }
.paper-ctl { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 8px; }
.paper-tg { display: inline-flex; align-items: center; gap: 4px; }
.paper-tg-label { font-size: 12.5px; color: var(--ink-soft); margin-right: 2px; }
.paper-tg-btn { border: 1px solid var(--border); background: transparent; color: var(--ink-soft); border-radius: 7px; padding: 4px 10px; font-size: 12.5px; cursor: pointer; }
.paper-tg-btn.on { background: #2563eb; color: #fff; border-color: #2563eb; }
.paper-repo { flex: 1 1 260px; min-width: 200px; font-size: 12.5px; padding: 5px 8px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); color: var(--ink); }
.paper-status { font-size: 12.5px; color: var(--ink); margin-top: 6px; }
.paper-warn { font-size: 12.5px; color: #b26a00; margin-top: 4px; }
.paper-proposal { margin-top: 8px; border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; background: color-mix(in srgb, #2563eb 5%, transparent); }
.paper-pp-head { font-size: 13px; font-weight: 600; }
.paper-pp-sum { font-size: 12px; white-space: pre-wrap; max-height: 260px; overflow: auto; margin: 6px 0; font-family: inherit; color: var(--ink-soft); }
.paper-rename { font-size: 12px; color: var(--ink-soft); }
'''


def main():
    patch("app.py", "papers card (fordycelab-pubs)", lambda s: (
        s.replace("def build_data():", APP_PY_BLOCK + "def build_data():", 1)
         .replace('    d["cy_drafts"] = load_json(CYDRAFTS, [])[:15]\n', '    d["cy_drafts"] = load_json(CYDRAFTS, [])[:15]\n    d["papers"] = papers_data()\n', 1)
         .replace('    else: raise ValueError("unknown action: %s" % t)', APP_PY_ACTIONS + '    else: raise ValueError("unknown action: %s" % t)', 1)
         .replace('          "/app.js": ("app.js", "application/javascript; charset=utf-8")}',
                  '          "/app.js": ("app.js", "application/javascript; charset=utf-8"),\n'
                  '          "/squarespace-instructions": (os.path.join(PUBSDIR, "squarespace", "INSTRUCTIONS-Squarespace.md"), "text/plain; charset=utf-8")}', 1)))
    patch("app.js", "papers card (fordycelab-pubs)", lambda s: (
        s.replace("  function load() {", APP_JS_BLOCK + "  function load() {", 1)
         .replace("      renderSchedule(d.schedule);\n", "      renderSchedule(d.schedule);\n      renderPapers(d.papers);\n", 1)))
    patch("app.html", 'id="papers"', lambda s: s.replace('  <div class="grid">', '  <div id="papers"></div>\n\n  <div class="grid">', 1))
    patch("style.css", "papers card (fordycelab-pubs)", lambda s: s.rstrip("\n") + "\n" + CSS_BLOCK)
    # sanity: compile app.py
    import py_compile
    py_compile.compile(os.path.join(SRV, "app.py"), doraise=True)
    print("ok - now restart:  launchctl kickstart -k gui/$(id -u)/com.cy.server")


if __name__ == "__main__":
    main()
