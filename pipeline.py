import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from audit import log_event
from detection import detect, decide

USERS = {
    "trader_1": "trader",
    "banker_1": "banker",
    "compliance_1": "compliance",
}

SAFE_BLOCK_MSG = "Blocked by information barrier policy."


@dataclass
class Ctx:
    user_id: Optional[str]
    messages: list
    request_id: str = field(default_factory=lambda: f"req_{uuid.uuid4().hex[:8]}")
    role: Optional[str] = None
    entities: list = field(default_factory=list)
    outcome: str = "allowed"  # allowed | redacted | blocked
    reason: str = ""
    sent_to_llm: str = ""
    vault: dict = field(default_factory=dict)
    output: str = ""
    done: bool = False
    timings: dict = field(default_factory=dict)


def block(ctx, reason):
    ctx.outcome = "blocked"
    ctx.reason = reason
    ctx.output = SAFE_BLOCK_MSG
    ctx.done = True


# ---- stages: each takes ctx and mutates it ----

def auth(ctx):
    ctx.role = USERS.get(ctx.user_id)
    if ctx.role is None:
        block(ctx, "unknown user")


def scan_prompt(ctx):
    ctx.entities = detect(ctx.messages[-1]["content"])


def policy(ctx):
    d = decide(ctx.role, ctx.entities)
    if d["outcome"] == "blocked":
        block(ctx, d["reason"])
    else:
        ctx.reason = d["reason"]


def tokenize(ctx):
    text = ctx.messages[-1]["content"]
    for i, e in enumerate(ctx.entities, 1):
        placeholder = f"[{e['type'].upper()}_{i}]"
        ctx.vault[placeholder] = e["text"]
        text = re.sub(re.escape(e["text"]), placeholder, text, flags=re.IGNORECASE)
    ctx.sent_to_llm = text
    if ctx.entities:
        ctx.outcome = "redacted"


def call_llm(ctx):
    # TODO: replace with a real model call (hosted or local)
    ctx.output = f"(stub LLM) I received: {ctx.sent_to_llm}"


def scan_response(ctx):
    d = decide(ctx.role, detect(ctx.output))
    if d["outcome"] == "blocked":
        block(ctx, "response contained restricted entity")


def detokenize(ctx):
    for placeholder, real in ctx.vault.items():
        ctx.output = ctx.output.replace(placeholder, real)


STAGES = [auth, scan_prompt, policy, tokenize, call_llm, scan_response, detokenize]


def run_pipeline(ctx):
    try:
        for stage in STAGES:
            if ctx.done:
                break
            t = time.perf_counter()
            stage(ctx)
            ctx.timings[stage.__name__] = round((time.perf_counter() - t) * 1000, 2)
    except Exception as e:  # fail closed
        block(ctx, f"internal error, failed closed ({type(e).__name__})")

    try:
        log_event({
            "request_id": ctx.request_id,
            "user": ctx.user_id,
            "role": ctx.role,
            "original_prompt": ctx.messages[-1]["content"] if ctx.messages else "",
            "sent_to_llm": ctx.sent_to_llm,
            "entities": [e["text"] for e in ctx.entities],
            "outcome": ctx.outcome,
            "reason": ctx.reason,
            "timings_ms": ctx.timings,
        })
    except Exception:
        pass  # logging must never break the request
    return ctx