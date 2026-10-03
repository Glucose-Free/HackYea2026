from datetime import datetime, timezone

EVENTS = []  # stub: Coder 3 swaps in SQLite + hash chain


def log_event(event):
    event["ts"] = datetime.now(timezone.utc).isoformat()
    EVENTS.append(event)


def get_events():
    return EVENTS