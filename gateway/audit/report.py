import html
from urllib.parse import quote

from gateway.audit.log import ChainVerification
from gateway.audit.query import FetchTotalsBucket, RequestPage, RequestTrace, UserFetchStats

PAGE_CSS = """
body{font-family:system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#1a1a1a;background:#fff}
h1{margin-bottom:.2rem}.sub{color:#666;margin-top:0}
table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}
th,td{border-bottom:1px solid #ddd;padding:.4rem .5rem;text-align:left;font-size:.9rem;vertical-align:top}
.ok{color:#0a7d32;font-weight:600}.bad{color:#b3261e;font-weight:600}.muted{color:#666}
.card{background:#f6f7f9;border-radius:8px;padding:.8rem 1rem;margin:.5rem 0}
code{background:#eee;padding:.1rem .3rem;border-radius:4px;word-break:break-all}
"""
REPORT_TITLE = "AI Control Gateway — Security report"
INCIDENT_TITLE = "Request trace {request_id}"
STEP_OUTCOME_CLASSES = {"denied": "bad", "failed": "bad", "passed": "ok"}
STEP_INDENT_REM = 1.2


def escape(value: object) -> str:
    return html.escape(str(value))


def build_page(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{PAGE_CSS}</style></head><body>{body}</body></html>"
    )


def build_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def build_chain_card(chain: ChainVerification) -> str:
    if chain.intact:
        return f"<div class='card'>Audit chain: <span class='ok'>intact</span> ({chain.checked} events)</div>"
    return f"<div class='card'>Audit chain: <span class='bad'>BROKEN</span> at <code>{escape(chain.first_broken)}</code></div>"


def build_report_page(
    chain: ChainVerification,
    totals: list[FetchTotalsBucket],
    user_stats: list[UserFetchStats],
    recent_denied: RequestPage,
    token: str,
) -> str:
    passed, denied = sum(bucket.passed for bucket in totals), sum(bucket.denied for bucket in totals)
    user_rows = [[escape(entry.user_name or entry.user_id), escape(entry.passed), escape(entry.denied), escape(entry.last_fetch_at.isoformat())] for entry in user_stats]
    denied_rows = [
        [
            f"<a href='/report/incident/{quote(item.request_id)}?token={quote(token)}'>{escape(item.request_id)}</a>",
            escape(item.user_name or item.user_id), escape(item.denied_at.value if item.denied_at else ""),
            escape(item.reason), escape(item.occurred_at.isoformat()),
        ]
        for item in recent_denied.items
    ]
    body = (
        f"<h1>{escape(REPORT_TITLE)}</h1><p class='sub'>Last 24 hours</p>"
        + build_chain_card(chain)
        + f"<div class='card'>Data fetches: <span class='ok'>{passed} passed</span> · <span class='bad'>{denied} denied</span></div>"
        + "<h2>Users</h2>" + build_table(["User", "Passed", "Denied", "Last fetch"], user_rows)
        + "<h2>Recent denied requests</h2>" + build_table(["Request", "User", "Denied at", "Reason", "Time"], denied_rows)
    )
    return build_page(REPORT_TITLE, body)


def build_incident_page(trace: RequestTrace) -> str:
    depth_by_step_id: dict[str, int] = {}
    rows = []
    for step in trace.steps:
        depth = depth_by_step_id.get(step.parent_step_id, -1) + 1 if step.parent_step_id else 0
        depth_by_step_id[step.step_id] = depth
        outcome_class = STEP_OUTCOME_CLASSES.get(step.outcome.value, "muted")
        rows.append([
            f"<span style='padding-left:{depth * STEP_INDENT_REM}rem'>{escape(step.kind.value)}</span>",
            escape(step.name),
            f"<span class='{outcome_class}'>{escape(step.outcome.value)}</span>",
            escape(step.reason),
            f"<code>{escape(step.detail)}</code>" if step.detail else "",
        ])
    summary = trace.summary
    title = INCIDENT_TITLE.format(request_id=summary.request_id)
    body = (
        f"<h1>{escape(title)}</h1>"
        f"<p class='sub'>{escape(summary.user_name or summary.user_id)} · {escape(summary.outcome.value)} · {escape(summary.occurred_at.isoformat())}</p>"
        + build_table(["Step", "Name", "Outcome", "Reason", "Detail"], rows)
    )
    return build_page(title, body)
