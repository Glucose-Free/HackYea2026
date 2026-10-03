import html
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LAST_PATH = os.path.join(HERE, "selftest_last.json")

CSS = """
body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}
h1{margin-bottom:.2rem}.sub{color:#666;margin-top:0}
table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}
th,td{border-bottom:1px solid #ddd;padding:.4rem .5rem;text-align:left;font-size:.9rem;vertical-align:top}
.ok{color:#0a7d32;font-weight:600}.bad{color:#b3261e;font-weight:600}.warn{color:#9a6700;font-weight:600}
.card{background:#f6f7f9;border-radius:8px;padding:.8rem 1rem;margin:.5rem 0}
code{background:#eee;padding:.1rem .3rem;border-radius:4px;word-break:break-all}
"""


def _esc(x):
    return html.escape(str(x))


def _page(title, body):
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_esc(title)}</title><style>{CSS}</style></head><body>{body}</body></html>"
    )


def _table(headers, rows):
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def run_selftest():
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "selftest.py"), "--json"],
        capture_output=True, text=True, timeout=120, cwd=HERE,
    )
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        out = {"passed": False, "results": [], "summary": {},
               "error": (proc.stderr or proc.stdout)[-500:]}
    out["ran_at"] = datetime.now(timezone.utc).isoformat()
    with open(LAST_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return out


def last_selftest():
    if not os.path.exists(LAST_PATH):
        return None
    with open(LAST_PATH, encoding="utf-8") as f:
        return json.load(f)


def _stats(events):
    by_type, by_sev, blocks_by_user, overheads = {}, {}, {}, []
    for e in events:
        t = e.get("type", "?")
        s = e.get("severity", "?")
        by_type[t] = by_type.get(t, 0) + 1
        by_sev[s] = by_sev.get(s, 0) + 1
        if e.get("outcome") == "blocked":
            u = e.get("user") or "unknown"
            blocks_by_user[u] = blocks_by_user.get(u, 0) + 1
        timings = e.get("timings_ms") or {}
        if timings:
            overheads.append(sum(v for k, v in timings.items() if k != "call_llm"))
    avg = round(sum(overheads) / len(overheads), 2) if overheads else 0
    return by_type, by_sev, blocks_by_user, avg


def build_report(events, chain, selftest, link_suffix=""):
    by_type, by_sev, blocks_by_user, avg = _stats(events)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [f"<h1>Security report</h1><p class='sub'>Generated {now}</p>"]

    # audit integrity
    if chain.get("intact"):
        msg = (f"<span class='ok'>Chain intact</span>: {chain.get('checked', 0)} events verified."
               f"<br>Head hash: <code>{_esc(chain.get('head'))}</code>")
    else:
        msg = f"<span class='bad'>Chain broken</span> at event {_esc(chain.get('first_broken'))}."
    parts.append(f"<h2>Audit integrity</h2><div class='card'>{msg}</div>")

    # self-test
    parts.append("<h2>Protection self-test</h2>")
    if not selftest:
        parts.append("<div class='card'>Not run yet. Open <code>/selftest</code> to run it.</div>")
    elif selftest.get("error"):
        parts.append(f"<div class='card'><span class='bad'>Self-test could not run</span><br>"
                     f"<code>{_esc(selftest['error'])}</code></div>")
    else:
        s = selftest.get("summary", {})
        cls, label = ("ok", "PASSING") if selftest.get("passed") else ("bad", "FAILING")
        parts.append(
            f"<div class='card'><span class='{cls}'>{label}</span> (last run {_esc(selftest.get('ran_at'))})<br>"
            f"Attacks blocked: {_esc(s.get('attacks_blocked'))} · "
            f"Benign wrongly blocked: {_esc(s.get('benign_wrongly_blocked'))} · "
            f"Known gaps: {_esc(s.get('known_gaps'))} · Pending: {_esc(s.get('pending'))}</div>"
        )
        colors = {"PASS": "ok", "FIXED": "ok", "FAIL": "bad", "GAP": "warn"}
        rows = [[f"<span class='{colors.get(r['result'], '')}'>{_esc(r['result'])}</span>",
                 _esc(r["kind"]), _esc(r["name"])] for r in selftest.get("results", [])]
        parts.append(_table(["Result", "Kind", "Scenario"], rows))

    # activity
    parts.append("<h2>Activity</h2>")
    parts.append(f"<div class='card'>{len(events)} requests · average gateway overhead "
                 f"{avg} ms (excluding the model call)</div>")
    parts.append(_table(["Event type", "Count"], [[_esc(k), v] for k, v in sorted(by_type.items())]))
    parts.append(_table(["Severity", "Count"], [[_esc(k), v] for k, v in sorted(by_sev.items())]))

    # users
    if blocks_by_user:
        rows = []
        for u, n in sorted(blocks_by_user.items(), key=lambda kv: -kv[1]):
            flag = "<span class='warn'>review: repeated blocks</span>" if n >= 3 else ""
            rows.append([_esc(u), n, flag])
        parts.append("<h2>Blocked requests by user</h2>" + _table(["User", "Blocked", "Flag"], rows))

    # recent blocked events
    blocked = [e for e in events if e.get("outcome") == "blocked"][-10:][::-1]
    if blocked:
        rows = [[f"<a href='/report/incident/{_esc(e['event_id'])}{link_suffix}'>{_esc(e['event_id'])}</a>",
                 _esc(str(e.get("ts", ""))[:19]), _esc(e.get("user")),
                 _esc(e.get("type")), _esc(e.get("reason"))] for e in blocked]
        parts.append("<h2>Recent blocked events</h2>" +
                     _table(["Event", "Time", "User", "Type", "Reason"], rows))

    return _page("Security report", "".join(parts))


def build_incident(e, link_suffix=""):
    rows = [
        ["Event", _esc(e.get("event_id"))],
        ["Time", _esc(e.get("ts"))],
        ["User / role", f"{_esc(e.get('user'))} / {_esc(e.get('role'))}"],
        ["Type / severity", f"{_esc(e.get('type'))} / {_esc(e.get('severity'))}"],
        ["Outcome", _esc(e.get("outcome"))],
        ["Reason", _esc(e.get("reason"))],
        ["Entities matched", _esc(", ".join(e.get("entities", [])))],
        ["Original prompt", f"<code>{_esc(e.get('original_prompt'))}</code>"],
        ["Sent to model", f"<code>{_esc(e.get('sent_to_llm') or '(nothing: the request never reached the model)')}</code>"],
    ]
    if e.get("chain"):
        rows.append(["Inference chain", "<br>".join(_esc(c) for c in e["chain"])])
    for key, label in (("risk_score", "Risk score"),
                       ("candidates_remaining", "Candidates remaining"),
                       ("policy_version", "Policy version")):
        if e.get(key) is not None:
            rows.append([label, _esc(e[key])])
    rows.append(["Entry hash", f"<code>{_esc(e.get('hash'))}</code>"])
    rows.append(["Previous hash", f"<code>{_esc(e.get('prev_hash'))}</code>"])
    body = (f"<h1>Incident {_esc(e.get('event_id'))}</h1>"
            f"<p class='sub'><a href='/report{link_suffix}'>back to report</a></p>"
            + _table(["Field", "Value"], rows))
    return _page("Incident " + str(e.get("event_id")), body)