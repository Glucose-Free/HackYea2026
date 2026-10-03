from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import report
from audit import get_events, verify_chain
from pipeline import USERS, Ctx, run_pipeline

app = FastAPI(title="Wall-Aware AI Gateway")


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: Optional[str] = "stub"
    messages: list[Message]


def require_compliance(header_user, query_user):
    # DEMO SHORTCUT: ?user= lets a browser open reports. Real deployments need real auth.
    uid = header_user or query_user
    if USERS.get(uid) != "compliance":
        raise HTTPException(status_code=403, detail="compliance role required")
    return uid


@app.post("/v1/chat/completions")
def chat(req: ChatRequest, x_user_id: Optional[str] = Header(default=None)):
    ctx = run_pipeline(Ctx(user_id=x_user_id, messages=[m.model_dump() for m in req.messages]))
    return {
        "request_id": ctx.request_id,
        "choices": [{"message": {"role": "assistant", "content": ctx.output}}],
        "decision": {
            "outcome": ctx.outcome,
            "reason": ctx.reason,
            "entities": [e["text"] for e in ctx.entities],
            "user": ctx.user_id,
            "role": ctx.role,
            "sent_to_llm": ctx.sent_to_llm,
            "timings_ms": ctx.timings,
        },
    }


@app.get("/audit")
def audit(x_user_id: Optional[str] = Header(default=None), user: Optional[str] = None):
    require_compliance(x_user_id, user)
    return get_events()


@app.get("/audit/verify")
def audit_verify():
    return verify_chain()


@app.get("/selftest")
def selftest(x_user_id: Optional[str] = Header(default=None), user: Optional[str] = None):
    require_compliance(x_user_id, user)
    return report.run_selftest()


@app.get("/report", response_class=HTMLResponse)
def security_report(x_user_id: Optional[str] = Header(default=None), user: Optional[str] = None):
    uid = require_compliance(x_user_id, user)
    return report.build_report(get_events(), verify_chain(), report.last_selftest(), f"?user={uid}")


@app.get("/report/incident/{event_id}", response_class=HTMLResponse)
def incident(event_id: str, x_user_id: Optional[str] = Header(default=None), user: Optional[str] = None):
    uid = require_compliance(x_user_id, user)
    for e in get_events():
        if e.get("event_id") == event_id:
            return report.build_incident(e, f"?user={uid}")
    raise HTTPException(status_code=404, detail="event not found")