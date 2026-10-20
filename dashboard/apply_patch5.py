#!/usr/bin/env python3
"""Patch 5 (2026-10-20): "✕ hide" on the Papers card. Hiding collapses the card to one line until
something changes (a new paper, a status change, a proposal ready). Re-open with "show".
  - app.py: papers_data() gains hidden_sig / sig; actions paper_hide / paper_show
  - app.js: renderPapers honours the signature
Idempotent + backups.
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
        print(fn, "already patched (5)")
        return
    shutil.copy(p, p + ".bak-papers5-" + TS)
    s2 = transform(s)
    if s2 == s:
        raise SystemExit("patch5 for %s did not apply (anchor not found)" % fn)
    open(p, "w").write(s2)
    print(fn, "patched (5)")


APP_PY = '''
def _papers_sig(items, flags):   # papers card: hide signature (fordycelab-pubs)
    import hashlib
    key = json.dumps([[i.get("id"), i.get("status"), bool(i.get("portal_proposal")), i.get("pdf_status")] for i in items] + [flags], sort_keys=True)
    return hashlib.md5(key.encode()).hexdigest()[:12]

PUBS_HIDE = os.path.join(PUBSDIR, "dashboard-hide.json")

def papers_data():
    items = load_json(PUBS_PENDING, [])
    dec = load_json(PUBS_DECISIONS, {})
    for it in items:
        it["decision"] = dec.get(it["id"], {})
    st = load_json(PUBS_STATE, {})
    flags = st.get("flags", {})
    sig = _papers_sig(items, flags)
    return {"items": items, "flags": flags, "last_poll": st.get("last_poll", ""), "sig": sig,
            "hidden": load_json(PUBS_HIDE, {}).get("sig") == sig}
'''

APP_JS_HIDE = r'''
    if (pd.hidden) {   // papers card: hidden until something changes (fordycelab-pubs)
      var mini = el("div", "paper-mini");
      mini.appendChild(document.createTextNode("📚 Papers: nothing new since you hid this card" + (pd.last_poll ? " · last check " + pd.last_poll : "") + " "));
      var show = el("a", "iact", "show"); show.href = "#";
      show.addEventListener("click", function (e) { e.preventDefault(); act({ type: "paper_show" }); setTimeout(load, 600); });
      mini.appendChild(show); host.appendChild(mini); return;
    }
'''


def main():
    def py(s):
        start = s.index("def papers_data():")
        end = s.index("def _pubs_run(")
        s = s[:start] + APP_PY.lstrip("\n") + "\n" + s[end:]
        s = s.replace('    elif t == "paper_poll":     _pubs_run("poll.py")\n',
                      '    elif t == "paper_poll":     _pubs_run("poll.py")\n'
                      '    elif t == "paper_hide":     save_json(PUBS_HIDE, {"sig": papers_data()["sig"], "ts": datetime.datetime.now().isoformat()})\n'
                      '    elif t == "paper_show":     save_json(PUBS_HIDE, {})\n', 1)
        return s
    patch("app.py", "papers card: hide signature", py)

    def js(s):
        s = s.replace('    var host = document.getElementById("papers"); if (!host) return; host.innerHTML = "";\n    pd = pd || {}; var list = pd.items || []; var flags = pd.flags || {};\n',
                      '    var host = document.getElementById("papers"); if (!host) return; host.innerHTML = "";\n    pd = pd || {}; var list = pd.items || []; var flags = pd.flags || {};\n' + APP_JS_HIDE, 1)
        s = s.replace('    right.appendChild(addb); right.appendChild(chk); right.appendChild(el("span", "count", String(list.length)));',
                      '    var hide = el("button", "tune", "✕ hide"); hide.title = "collapse this card until something new happens";\n'
                      '    hide.addEventListener("click", function () { act({ type: "paper_hide" }); host.innerHTML = ""; toast("hidden until something changes"); setTimeout(load, 800); });\n'
                      '    right.appendChild(addb); right.appendChild(chk); right.appendChild(hide); right.appendChild(el("span", "count", String(list.length)));', 1)
        return s
    patch("app.js", "hidden until something changes (fordycelab-pubs)", js)
    patch("style.css", ".paper-mini", lambda s: s.rstrip("\n") + "\n.paper-mini { font-size: 12.5px; color: var(--ink-faint); padding: 6px 2px 14px; }\n.paper-mini a { color: var(--cyan); margin-left: 4px; }\n")
    import py_compile
    py_compile.compile(os.path.join(SRV, "app.py"), doraise=True)
    print("ok - now restart:  launchctl kickstart -k gui/$(id -u)/com.cy.server")


if __name__ == "__main__":
    main()
