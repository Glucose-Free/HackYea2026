import hashlib
import json
import os
import threading
from datetime import datetime, timezone

LOG_PATH = os.environ.get("AUDIT_LOG_PATH", "audit.jsonl")
GENESIS = "0" * 64
_lock = threading.Lock()


def _hash(prev_hash, body):
    payload = prev_hash + json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_all():
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _load_state():
    events = _read_all()
    return (events[-1]["hash"] if events else GENESIS), len(events)


_last_hash, _count = _load_state()


def _classify(e):
    outcome, reason = e.get("outcome"), e.get("reason", "")
    if outcome == "allowed":
        return "ALLOWED", "info"
    if outcome == "redacted":
        return "REDACTED", "info"
    if "failed closed" in reason or "unavailable" in reason:
        return "FAILED_CLOSED", "medium"
    if "inference" in reason:
        return "BLOCKED_INFERENCE", "high"
    return "BLOCKED_POLICY", "medium"


def log_event(event):
    global _last_hash, _count
    with _lock:
        body = dict(event)
        etype, severity = _classify(body)
        body.setdefault("type", etype)
        body.setdefault("severity", severity)
        _count += 1
        body["event_id"] = f"evt_{_count:05d}"
        body["ts"] = datetime.now(timezone.utc).isoformat()
        body["prev_hash"] = _last_hash
        body["hash"] = _hash(_last_hash, body)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(body) + "\n")
        _last_hash = body["hash"]


def get_events():
    return _read_all()


def verify_chain():
    prev, n = GENESIS, 0
    for e in _read_all():
        n += 1
        stored = e.get("hash")
        body = {k: v for k, v in e.items() if k != "hash"}
        if e.get("prev_hash") != prev or _hash(prev, body) != stored:
            return {"intact": False, "first_broken": e.get("event_id", n), "checked": n}
        prev = stored
    return {"intact": True, "checked": n, "head": prev}
