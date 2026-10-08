#!/usr/bin/env python3
"""Patch 2 for the Cy dashboard: PDF upload from the New Papers card.
POST /api/paper_pdf?name=<expected filename>  (body = the PDF bytes) -> ~/fordycelab-pubs/pdf-inbox/<name>
then publish.py --sweep attaches it to the waiting entry. Idempotent (markers) + backups.
Run on the mini:  python3 ~/fordycelab-pubs/dashboard/apply_patch2.py && launchctl kickstart -k gui/$(id -u)/com.cy.server
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
        print(fn, "already patched (2)")
        return
    shutil.copy(p, p + ".bak-papers2-" + TS)
    s2 = transform(s)
    if s2 == s:
        raise SystemExit("patch2 for %s did not apply (anchor not found)" % fn)
    open(p, "w").write(s2)
    print(fn, "patched (2)")


APP_PY_ROUTE = '''    def do_POST(self):
        if self.path.startswith("/api/paper_pdf"):   # papers card: PDF upload (fordycelab-pubs)
            try:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                name = os.path.basename((q.get("name") or ["upload.pdf"])[0])
                if not name.lower().endswith(".pdf"): name += ".pdf"
                n = int(self.headers.get("Content-Length", 0))
                data = self.rfile.read(n)
                if not data.startswith(b"%PDF-"): raise ValueError("that file is not a PDF")
                inbox = os.path.join(PUBSDIR, "pdf-inbox"); os.makedirs(inbox, exist_ok=True)
                with open(os.path.join(inbox, name), "wb") as f: f.write(data)
                _pubs_run("publish.py", "--sweep")
                return self._send(200, json.dumps({"ok": True, "saved": name}))
            except Exception as e:
                return self._send(200, json.dumps({"ok": False, "error": str(e)}))
'''

APP_JS_UPLOAD = r'''
  // papers card: PDF upload (fordycelab-pubs)
  function pdfUploadBtn(row, it, kind) {
    var name = kind === "preprint" ? it.preprint_pdf_expected : it.pdf_expected;
    if (!name) return;
    var wrap = el("div", "paper-ctl");
    var inp = document.createElement("input"); inp.type = "file"; inp.accept = "application/pdf"; inp.style.display = "none";
    var btn = el("button", "btn btn-primary", "📄 Upload the " + (kind === "preprint" ? "preprint" : "published") + " PDF");
    btn.style.fontSize = "12px"; btn.style.padding = "6px 12px";
    btn.addEventListener("click", function () { inp.click(); });
    inp.addEventListener("change", function () {
      var f = inp.files[0]; if (!f) return;
      btn.textContent = "uploading…"; btn.disabled = true;
      fetch("/api/paper_pdf?name=" + encodeURIComponent(name), { method: "POST", body: f })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (!j.ok) throw new Error(j.error || "upload failed");
          toast("PDF saved — attaching it to the entry"); btn.textContent = "✓ uploaded"; setTimeout(load, 12000);
        })
        .catch(function (e) { toast(String(e.message || e), true); btn.textContent = "📄 Upload again"; btn.disabled = false; });
    });
    wrap.appendChild(btn); wrap.appendChild(inp); row.appendChild(wrap);
  }
'''


def main():
    patch("app.py", "papers card: PDF upload (fordycelab-pubs)", lambda s: s.replace("    def do_POST(self):\n", APP_PY_ROUTE, 1))
    patch("app.js", "papers card: PDF upload (fordycelab-pubs)", lambda s: (
        s.replace("  function renderPapers(pd) {", APP_JS_UPLOAD + "  function renderPapers(pd) {", 1)
         .replace('      if (it.pdf_status) row.appendChild(el("div", "paper-warn", "📄 " + it.pdf_status));\n',
                  '      if (it.pdf_status) { row.appendChild(el("div", "paper-warn", "📄 " + it.pdf_status)); pdfUploadBtn(row, it, "published"); }\n', 1)
         .replace('      if (it.preprint_pdf_status) row.appendChild(el("div", "paper-warn", "📄 " + it.preprint_pdf_status));\n',
                  '      if (it.preprint_pdf_status) { row.appendChild(el("div", "paper-warn", "📄 " + it.preprint_pdf_status)); pdfUploadBtn(row, it, "preprint"); }\n', 1)))
    import py_compile
    py_compile.compile(os.path.join(SRV, "app.py"), doraise=True)
    print("ok - now restart:  launchctl kickstart -k gui/$(id -u)/com.cy.server")


if __name__ == "__main__":
    main()
