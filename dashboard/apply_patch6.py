#!/usr/bin/env python3
"""Patch 6 (2026-10-20): "↻ retry proposal" on a paper whose data-portal proposal failed
(e.g. API credit ran out). Idempotent + backups."""
import datetime
import os
import shutil

SRV = os.path.expanduser("~/cy/server")
TS = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def patch(fn, marker, transform):
    p = os.path.join(SRV, fn)
    s = open(p).read()
    if marker in s:
        print(fn, "already patched (6)")
        return
    shutil.copy(p, p + ".bak-papers6-" + TS)
    s2 = transform(s)
    if s2 == s:
        raise SystemExit("patch6 for %s did not apply (anchor not found)" % fn)
    open(p, "w").write(s2)
    print(fn, "patched (6)")


JS = r'''      if (it.portal_status && String(it.portal_status).indexOf("proposal failed") === 0) {   // retry (fordycelab-pubs)
        var rt = el("div", "paper-ctl"); var rb = el("button", "tune", "↻ retry the data-portal proposal");
        rb.addEventListener("click", function () { act({ type: "paper_portal_retry", id: it.id }); rb.textContent = "retrying…"; rb.disabled = true; setTimeout(load, 15000); });
        rt.appendChild(rb); row.appendChild(rt);
      }
'''


def main():
    patch("app.py", 'elif t == "paper_portal_retry"', lambda s: s.replace(
        '    elif t == "paper_show":',
        '    elif t == "paper_portal_retry":   # papers card: retry a failed proposal (fordycelab-pubs)\n'
        '        _pubs_run("ingest_propose.py", a["id"])\n'
        '    elif t == "paper_show":', 1))
    patch("app.js", "retry (fordycelab-pubs)", lambda s: s.replace(
        '      if (it.portal_proposal) {\n        var pp = it.portal_proposal;',
        JS + '      if (it.portal_proposal) {\n        var pp = it.portal_proposal;', 1))
    import py_compile
    py_compile.compile(os.path.join(SRV, "app.py"), doraise=True)
    print("ok - now restart:  launchctl kickstart -k gui/$(id -u)/com.cy.server")


if __name__ == "__main__":
    main()
