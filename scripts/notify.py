#!/usr/bin/env python3
"""notify.py - email Polly (from her own Gmail, to herself) about new papers waiting for a
decision, what was published, and any one-time Squarespace step still pending.

  notify.py --poll         after poll.py: list new/waiting papers (only sends if there is something)
  notify.py --publish      after publish.py: what was published / what still needs a PDF
  notify.py --portal ID    after portal_merge.py
"""
import os
import sys

from common import CFG, DONE, PENDING, STATE, STATUS, esc, gmail_send, load_json, log, now_label


def sq_reminder(flags):
    lines = []
    if not flags.get("pubs_switched"):
        lines.append("Publications page: the one-time switch to the auto-updating Code Block has NOT been done yet, so "
                     "website changes are staged on GitHub but not visible on fordycelab.com.")
    if not flags.get("data_switched"):
        lines.append("Data page: the counters are still hard-coded; the one-time Code Block swap has NOT been done yet.")
    if not lines:
        return ""
    return ("<p style='background:#fff4e5;padding:8px 12px;border-left:3px solid #f0a020'><b>Reminder (one-time, needs a Squarespace login):</b><br>"
            + "<br>".join(esc(l) for l in lines) + "<br>Instructions: <a href='%s/squarespace-instructions'>%s/squarespace-instructions</a> "
            "(this is written so Ana can do it).</p>") % (CFG["dashboard_url"], CFG["dashboard_url"])


def paper_line(it):
    t = esc(it.get("title", ""))
    url = it.get("web_url", "")
    kind = it.get("type", "")
    where = "%s (%s)" % (esc(it.get("venue", "")), it.get("year", ""))
    v = "" if it.get("verified") else " <i>(author match not verified - please check)</i>"
    m = (" - " + esc(it["merge_note"])) if it.get("merge_note") else ""
    st = esc(it.get("status", ""))
    return "<li><a href='%s'>%s</a> - %s, %s%s%s<br><span style='color:#666'>%s</span></li>" % (url, t, kind, where, v, m, st)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--poll"
    pending = load_json(PENDING, [])
    state = load_json(STATE, {})
    flags = state.get("flags", {})
    dash = CFG["dashboard_url"]
    if mode == "--poll":
        new_ids = set(state.get("last_poll_new", []))
        new = [p for p in pending if p["id"] in new_ids]
        waiting = [p for p in pending if p["id"] not in new_ids and p.get("status", "").startswith("awaiting")]
        if not new and not waiting and flags.get("pubs_switched") and flags.get("data_switched"):
            log("notify: nothing to report")
            return
        body = "<p>Hi Polly - biweekly paper check (%s).</p>" % now_label()
        if new:
            body += "<p><b>%d new paper%s found</b> - tell me what to do with each on the dashboard: <a href='%s'>%s</a></p><ul>%s</ul>" % (
                len(new), "" if len(new) == 1 else "s", dash, dash, "".join(paper_line(p) for p in new))
        if waiting:
            body += "<p><b>Still waiting for your decision</b> (from an earlier check):</p><ul>%s</ul>" % "".join(paper_line(p) for p in waiting)
        if not new and not waiting:
            body += "<p>No new papers or preprints since last time.</p>"
        body += sq_reminder(flags)
        body += "<p style='color:#888;font-size:12px'>Sources checked: OpenAlex, PubMed, arXiv, Google Scholar alert emails. Cy</p>"
        subj = "📚 %d new paper%s to review" % (len(new), "" if len(new) == 1 else "s") if new else "📚 Paper check: nothing new" + (" (Squarespace step pending)" if not (flags.get("pubs_switched") and flags.get("data_switched")) else "")
    elif mode == "--publish":
        s = load_json(STATUS, {}).get("last_publish", {})
        if not (s.get("applied") or s.get("swept") or s.get("errors")):
            return
        body = "<p>Website update (%s):</p>" % s.get("ts", "")
        if s.get("applied"):
            body += "<ul>" + "".join("<li><b>%s</b><br>%s</li>" % (esc(a.get("title", "")), "<br>".join(esc(n) for n in a.get("notes", []))) for a in s["applied"]) + "</ul>"
        if s.get("swept"):
            body += "<p>PDF picked up from pdf-inbox and entry updated for: %s</p>" % esc(", ".join(s["swept"]))
        if s.get("errors"):
            body += "<p style='color:#b00'>Errors: %s</p>" % esc("; ".join(s["errors"]))
        body += "<p>GitHub: %s. Live page: <a href='%s'>%s</a></p>" % (esc(s.get("git", "")), CFG["site_pubs_url"], CFG["site_pubs_url"])
        body += sq_reminder(flags)
        subj = "📚 Website updated: " + ", ".join((a.get("title") or "")[:40] for a in s.get("applied", []))[:120] if s.get("applied") else "📚 Website: PDF/link update"
    elif mode == "--portal":
        pid = sys.argv[2] if len(sys.argv) > 2 else ""
        it = next((p for p in pending if p["id"] == pid), None)
        m = (it or {}).get("portal_merged", {})
        body = "<p>Data Portal updated for <b>%s</b>: %s rows added under study key <code>%s</code>. Git: %s.<br>Counters regenerate automatically once the Data page Code Block is switched.</p>" % (
            esc((it or {}).get("title", pid)), m.get("rows", "?"), esc(m.get("p", "")), esc(m.get("git", "")))
        body += sq_reminder(flags)
        subj = "📊 Data Portal updated"
    else:
        return
    if os.environ.get("PUBS_NO_NOTIFY"):
        log("notify: suppressed (PUBS_NO_NOTIFY) '%s'" % subj)
        (STATUS.parent / "logs" / "last_email.html").write_text("<h3>%s</h3>%s" % (subj, body))
        return
    try:
        gmail_send(CFG["notify_to"], subj, body)
        log("notify: sent '%s'" % subj)
    except Exception as e:
        log("notify: send failed (%s)" % e)


if __name__ == "__main__":
    main()
