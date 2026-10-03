from typing import Optional

from fastapi import FastAPI, Header
from pydantic import BaseModel

from audit import get_events, verify_chain
from pipeline import Ctx, run_pipeline

app = FastAPI(title="Wall-Aware AI Gateway")


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: Optional[str] = "stub"
    messages: list[Message]


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



@app.get("/audit/verify")
def audit_verify():
    return verify_chain()