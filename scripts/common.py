#!/usr/bin/env python3
"""Shared helpers for the Fordyce Lab biweekly paper sync.

Everything is stdlib-only so it runs on the Mac mini's /usr/bin/python3 (3.9).
"""
import datetime
import fcntl
import html as htmlmod
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.path.expanduser("~"))

DEFAULT_CFG = {
    "openalex_author_id": "A5085863617",
    "contact_email": "fordyce@stanford.edu",
    "lookback_days": 180,
    "pages_base_url": "https://fordycelab.github.io/fordycelab-pubs",
    "site_pubs_url": "https://www.fordycelab.com/publications-v2",
    "site_data_url": "https://www.fordycelab.com/data",
    "data_portal_dir": str(HOME / "data-portal"),
    "data_portal_base_url": "https://fordycelab.github.io/data-portal",
    "pdf_inbox": str(HOME / "pfordyce@stanford.edu - Google Drive" / "My Drive" / "claude" / "lab-publications-site" / "pdf-inbox"),
    "notify_to": "fordyce@gmail.com",
    "dashboard_url": "http://pollys-mac-mini:8787",
    "scholar_alerts": True,
    "arxiv": True,
    "claude_model": "claude-sonnet-5",
    "anthropic_key_file": str(HOME / ".config" / "anthropic" / "key"),
    "gmail_credentials": str(HOME / ".claude" / "gmail-mcp" / "credentials.json"),
    "gmail_oauth_keys": str(HOME / ".claude" / "google-calendar-mcp" / "gcp-oauth.keys.json"),
    "git_push": True,
}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    p = ROOT / "config.json"
    if p.exists():
        try:
            cfg.update(json.load(open(p)))
        except Exception as e:
            log("config.json unreadable: %s" % e)
    return cfg


CFG = load_cfg()
UA = "fordycelab-pubs/1.0 (mailto:%s)" % CFG["contact_email"]

PUBS = ROOT / "publications.json"
STATE = ROOT / "state.json"
PENDING = ROOT / "pending.json"
DECISIONS = ROOT / "decisions.json"
DONE = ROOT / "done.json"
PDFS = ROOT / "pdfs"
INGEST = ROOT / "ingest"
LOGS = ROOT / "logs"
STATUS = ROOT / "status.json"

PORTAL_FIELDS = ["p", "y", "pw", "pm", "lib", "lig", "mt", "v", "u", "e", "et", "lim", "pv", "m", "n"]


# --------------------------------------------------------------------------- logging
def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] %s" % (ts, msg)
    print(line, flush=True)
    try:
        LOGS.mkdir(exist_ok=True)
        with open(LOGS / "run.log", "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- json + locking
@contextmanager
def locked(path):
    LOGS.mkdir(exist_ok=True)
    lock = LOGS / (Path(path).name + ".lock")
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj, indent=2):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def update_pending(fn):
    """Atomically mutate pending.json (a list of candidate dicts) under a lock."""
    with locked(PENDING):
        items = load_json(PENDING, [])
        out = fn(items)
        save_json(PENDING, items if out is None else out)


def pending_by_id():
    return {it["id"]: it for it in load_json(PENDING, [])}


# --------------------------------------------------------------------------- http
def http(url, headers=None, data=None, method=None, timeout=60, retries=3, backoff=3.0, ok_codes=(200,)):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=h, data=data, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                if ok_codes != "any" and r.status not in ok_codes:
                    raise urllib.error.HTTPError(url, r.status, "bad status", r.headers, None)
                return body, r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 401, 403, 404, 410):
                raise
        except Exception as e:  # URLError, timeout, etc.
            last = e
        time.sleep(backoff * (i + 1))
    raise last


def get_json(url, **kw):
    body, _, _ = http(url, headers={"Accept": "application/json"}, **kw)
    return json.loads(body.decode("utf-8", "replace"))


def head_ok(url, timeout=25):
    """Return (ok, code, note). Publishers often block bots; a 403 is reported, not treated as broken."""
    try:
        body, code, hdrs = http(url, timeout=timeout, retries=1, ok_codes="any")
        if code < 400:
            return True, code, ""
        return False, code, "HTTP %d" % code
    except urllib.error.HTTPError as e:
        if e.code in (403, 429, 503):
            return True, e.code, "publisher blocks scripts (HTTP %d) - link kept, spot-check in a browser" % e.code
        return False, e.code, "HTTP %d" % e.code
    except Exception as e:
        return False, 0, str(e)[:120]


def download(url, dest, timeout=120):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    body, code, hdrs = http(url, timeout=timeout, retries=2, ok_codes="any")
    if code >= 400:
        raise RuntimeError("HTTP %d for %s" % (code, url))
    with open(dest, "wb") as f:
        f.write(body)
    return dest, hdrs.get("Content-Type", "")


def is_pdf(path):
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except Exception:
        return False


# --------------------------------------------------------------------------- identifiers + text
DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s\"'<>#?]+)", re.I)


def norm_doi(s):
    if not s:
        return ""
    s = s.strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    return s.rstrip(".,;)")


def doi_from_url(url):
    if not url:
        return ""
    m = DOI_RE.search(url)
    if not m:
        return ""
    doi = m.group(1)
    # bioRxiv/medRxiv content URLs carry a version + suffix: 10.1101/2024.11.06.622305v2.full
    doi = re.sub(r"v\d+(\.full|\.abstract|\.article-metrics|\.supplementary-material)?$", "", doi)
    doi = re.sub(r"(\.full|\.abstract|\.article-metrics)$", "", doi)
    return norm_doi(doi)


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "")


def norm_title(t):
    t = htmlmod.unescape(strip_tags(t or ""))
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-z0-9 ]+", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def title_sim(a, b):
    ta = set(w for w in norm_title(a).split() if len(w) > 2)
    tb = set(w for w in norm_title(b).split() if len(w) > 2)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def slug(s, n=60):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return s[:n].rstrip("-")


def esc(s):
    return htmlmod.escape(s or "", quote=False)


# --------------------------------------------------------------------------- authors
PARTICLES = {"van", "von", "de", "del", "della", "di", "da", "der", "den", "le", "la", "du", "dos", "das", "ter", "ten"}


def split_name(display):
    """'Polly M. Fordyce' -> ('Fordyce', 'P.M.'); 'Po-Ssu Huang' -> ('Huang', 'P.-S.'); 'Alexander van Oudenaarden' -> ('van Oudenaarden', 'A.')"""
    name = (display or "").strip().replace("‐", "-").replace("–", "-")
    if "," in name:  # already 'Surname, Given'
        sur, given = [x.strip() for x in name.split(",", 1)]
    else:
        parts = name.split()
        if not parts:
            return "", ""
        i = len(parts) - 1
        while i > 0 and parts[i - 1].lower().strip(".") in PARTICLES:
            i -= 1
        sur = " ".join(parts[i:])
        given = " ".join(parts[:i])
    inits = []
    for g in given.replace(".", ". ").split():
        g = g.strip(".")
        if not g:
            continue
        if "-" in g:
            inits.append("-".join(x[0].upper() + "." for x in g.split("-") if x))
        else:
            inits.append(g[0].upper() + ".")
    return sur, "".join(inits)


def fmt_author(display):
    sur, inits = split_name(display)
    if not sur:
        return ""
    return "%s, %s" % (sur, inits) if inits else sur


def is_pi(display):
    sur, inits = split_name(display)
    return sur.lower() == "fordyce" and inits.startswith("P")


PI_HTML = '<strong>Fordyce, P.M.</strong>'   # matches the convention in the live publications.json


def authors_html(authors):
    """authors: list of {name, corresponding(bool), equal(bool)}. Mirrors the site's style:
    'A, B.B., C, D.D.‡, & <u><b>Fordyce, P.M.</b></u>‡'  with ‡ only when 2+ corresponding authors."""
    n_corr = sum(1 for a in authors if a.get("corresponding"))
    mark_corr = n_corr >= 2
    out = []
    for a in authors:
        s = PI_HTML if is_pi(a["name"]) else esc(fmt_author(a["name"]))
        if a.get("equal"):
            s += "*"
        if mark_corr and a.get("corresponding"):
            s += "‡"
        out.append(s)
    if len(out) > 1:
        out[-1] = "&amp; " + out[-1]
    txt = ", ".join(out)
    notes = []
    if any(a.get("equal") for a in authors):
        notes.append("* = equal contribution")
    if mark_corr:
        notes.append("‡ = co-corresponding")
    return txt, notes


# --------------------------------------------------------------------------- git
def git(args, cwd, check=True):
    r = subprocess.run(["/usr/bin/git"] + list(args), cwd=str(cwd), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed: %s %s" % (" ".join(args), r.stdout, r.stderr))
    return r


def git_commit_push(cwd, paths, message):
    """Commit the given paths if changed; push if git_push is enabled. Returns (committed, pushed, note)."""
    r = git(["status", "--porcelain", "--"] + [str(p) for p in paths], cwd)
    if not r.stdout.strip():
        return False, False, "no change"
    git(["add", "--"] + [str(p) for p in paths], cwd)
    git(["commit", "-q", "-m", message], cwd)
    if not CFG.get("git_push", True):
        return True, False, "committed (push disabled in config)"
    p = git(["push", "-q"], cwd, check=False)
    if p.returncode != 0:
        return True, False, "committed but push FAILED: %s" % (p.stderr.strip()[-300:])
    return True, True, "pushed"


# --------------------------------------------------------------------------- gmail (same token logic as the Cy dashboard)
_gtok = {"t": None, "exp": None}


def gmail_token():
    now = datetime.datetime.now()
    if _gtok["t"] and _gtok["exp"] and now < _gtok["exp"]:
        return _gtok["t"]
    c = json.load(open(CFG["gmail_credentials"]))

    def find(o, k):
        if isinstance(o, dict):
            if k in o and o[k]:
                return o[k]
            for v in o.values():
                r = find(v, k)
                if r:
                    return r
        return None

    keys = json.load(open(CFG["gmail_oauth_keys"]))
    cfg = keys.get("installed") or keys.get("web") or {}
    data = urllib.parse.urlencode({"client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                                   "refresh_token": find(c, "refresh_token"), "grant_type": "refresh_token"}).encode()
    at = json.load(urllib.request.urlopen(urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=data, method="POST"), timeout=25))["access_token"]
    _gtok["t"] = at
    _gtok["exp"] = now + datetime.timedelta(minutes=50)
    return at


def gmail_api(path, params=None, method="GET", body=None):
    url = "https://gmail.googleapis.com/gmail/v1/users/me/" + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": "Bearer " + gmail_token(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode())


def gmail_send(to, subject, html_body):
    import base64
    from email.mime.text import MIMEText
    msg = MIMEText(html_body, "html", "utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return gmail_api("messages/send", method="POST", body={"raw": raw})


# --------------------------------------------------------------------------- misc
def today():
    return datetime.date.today().isoformat()


def now_label():
    return datetime.datetime.now().strftime("%a %b %-d, %-I:%M %p")


def data_label(url, default="data"):
    """Button label for a repository link, matching the site's existing convention."""
    u = (url or "").lower()
    for k, v in (("osf.io", "OSF data"), ("zenodo", "Zenodo"), ("figshare", "figshare"), ("github.com", "GitHub"),
                 ("gitlab", "GitLab"), ("dryad", "Dryad"), ("purl.stanford.edu", "Stanford Digital Repository"),
                 ("huggingface", "data"), ("mendeley", "data")):
        if k in u:
            return v
    return default


def first_author_of(authors_html_):
    return htmlmod.unescape(strip_tags(authors_html_ or "")).split(",")[0].strip()


def load_pubs():
    """publications.json in the live page's format: {updated, count, publications:[...]}."""
    d = load_json(PUBS, {})
    if "publications" not in d:
        d = {"updated": today(), "count": 0, "publications": []}
    return d


def anthropic_key():
    try:
        return open(CFG["anthropic_key_file"]).read().strip()
    except Exception:
        return ""
