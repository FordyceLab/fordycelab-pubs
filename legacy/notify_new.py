#!/usr/bin/env python3
"""Email Polly when the publications feed gains a NEW paper.

Runs right after update_publications.py in run_and_publish.sh. Tracks every publication id ever
seen in seen-dois.txt (robust to OpenAlex returning an unstable set between runs — a paper that
briefly drops out and returns will NOT re-notify). On the first run it just seeds the file and
sends nothing (no blast). Sends through the mini's Cy Gmail access (auto-send is what Polly asked
for here). Never raises into the publish — email/errors are logged and swallowed.

Usage:  python3 notify_new.py            # normal: notify on new papers, update seen
        python3 notify_new.py --seed     # mark all current papers as seen, send nothing
        python3 notify_new.py --test     # send a sample notification (newest paper), don't touch seen
"""
import importlib.util, os, sys, json, base64, datetime
from email.message import EmailMessage

HOME = os.path.expanduser("~"); SITE = os.path.join(HOME, "lab-publications-site")
PUBS = os.path.join(SITE, "publications.json")
SEEN = os.path.join(SITE, "seen-dois.txt")
LOG = os.path.join(SITE, "last_run.log")
SEED = "--seed" in sys.argv; TEST = "--test" in sys.argv

spec = importlib.util.spec_from_file_location("ca", os.path.join(HOME, "cy", "agent", "cy_agent.py"))
ca = importlib.util.module_from_spec(spec); spec.loader.exec_module(ca)

def log(m):
    try:
        with open(LOG, "a") as f: f.write("%s [pubs-notify] %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), m))
    except Exception: pass

def key(p): return (p.get("doi") or p.get("id") or p.get("title") or "").strip().lower()

def load_pubs():
    return json.load(open(PUBS)).get("publications", [])

def seen_set():
    try: return set(x.strip() for x in open(SEEN) if x.strip())
    except Exception: return set()

def save_seen(ids):
    with open(SEEN, "w") as f:
        f.write("\n".join(sorted(ids)) + "\n")

def notify_to():
    return ca.cfg().get("pubs_notify_email") or "fordyce@gmail.com"

def send(subject, body):
    at = ca.gmail_token()
    msg = EmailMessage(); msg["To"] = notify_to(); msg["Subject"] = subject; msg.set_content(body)
    ca.gapi(at, "POST", "users/me/messages/send", {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()})

def describe(p):
    line = "• %s" % (p.get("title") or "(untitled)")
    meta = ", ".join(x for x in [p.get("venue"), str(p.get("year") or "")] if x)
    if meta: line += "\n  %s" % meta
    links = " | ".join("%s: %s" % (l.get("label"), l.get("url")) for l in p.get("links", []) if l.get("url"))
    if links: line += "\n  %s" % links
    return line

def main():
    pubs = load_pubs()
    cur = {key(p): p for p in pubs if key(p)}

    if TEST:
        p = max(pubs, key=lambda x: (x.get("year") or 0))
        send("Fordyce Lab site: publication-notification test",
             "This is a test of the new-paper email notification.\n\nMost recent entry on the feed:\n\n" + describe(p)
             + "\n\nGoing forward you'll get an email like this whenever the weekly update adds a paper.")
        log("sent TEST notification to %s" % notify_to()); print("sent test email to", notify_to()); return

    seen = seen_set()
    if SEED or not seen:
        save_seen(set(cur))
        log("seeded seen-dois.txt with %d ids (no email)" % len(cur)); print("seeded %d ids" % len(cur)); return

    new_ids = [k for k in cur if k not in seen]
    if new_ids:
        new = [cur[k] for k in new_ids]
        new.sort(key=lambda x: (-(x.get("year") or 0), x.get("title") or ""))
        n = len(new)
        body = ("The weekly Fordyce Lab publications update just added %d new %s to the site:\n\n%s\n\n"
                "Live at fordycelab.com/publications-v2 (auto-updates weekly)."
                % (n, "paper" if n == 1 else "papers", "\n\n".join(describe(p) for p in new)))
        try:
            send("Fordyce Lab site: %d new %s added" % (n, "paper" if n == 1 else "papers"), body)
            log("emailed %s about %d new paper(s): %s" % (notify_to(), n, "; ".join((p.get('title') or '')[:40] for p in new)))
            print("notified about %d new paper(s)" % n)
        except Exception as e:
            log("EMAIL FAILED (%r) for %d new paper(s)" % (e, n)); print("email failed: %r" % e)
    else:
        log("no new papers; nothing to email"); print("no new papers")
    save_seen(set(cur) | seen)

if __name__ == "__main__":
    try: main()
    except Exception as e:
        log("ERROR %r" % e); print("notify ERROR %r" % e)
