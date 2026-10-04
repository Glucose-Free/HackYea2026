import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64
EVENT_ID_FORMAT = "evt_{number:05d}"
BROKEN_LINE_FORMAT = "line {line_number}"
UNREADABLE_LINE_MESSAGE = "skipping unreadable audit log line %d"

OUTCOME_ANSWERED = "answered"
OUTCOME_REFUSED = "refused"
DENIED_AT_CHECKPOINT_2 = "checkpoint_2"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChainVerification:
    intact: bool
    checked: int
    first_broken: str | None
    head: str | None


@dataclass(frozen=True)
class EventClassification:
    event_type: str
    severity: str


def classify_event(event: dict[str, Any]) -> EventClassification:
    outcome = event.get("outcome")
    if outcome == OUTCOME_ANSWERED:
        if event.get("denied_at") == DENIED_AT_CHECKPOINT_2:
            return EventClassification("FETCH_DENIED", "medium")
        return EventClassification("ANSWERED", "info")
    if outcome == OUTCOME_REFUSED:
        return EventClassification("REFUSED", "medium")
    return EventClassification("FAILED_CLOSED", "medium")


def parse_event_line(line: str) -> dict[str, Any] | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def compute_event_hash(previous_hash: str, body: dict[str, Any]) -> str:
    payload = previous_hash + json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditLog:
    """Append-only JSONL log; each event carries the hash of the previous one, so edits break the chain."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        events = self.read_events()
        # A hand-edited last line without a hash must not stop the gateway from starting; verify_chain reports it.
        self._last_hash = events[-1].get("hash", GENESIS_HASH) if events else GENESIS_HASH
        self._count = len(events)

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            body = dict(event)
            classification = classify_event(body)
            body.setdefault("type", classification.event_type)
            body.setdefault("severity", classification.severity)
            self._count += 1
            body["event_id"] = EVENT_ID_FORMAT.format(number=self._count)
            body["ts"] = datetime.now(UTC).isoformat()
            body["prev_hash"] = self._last_hash
            body["hash"] = compute_event_hash(self._last_hash, body)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # A crash mid-write can leave the last line unterminated; without this the next event would merge into it.
            separator = "\n" if self._has_unterminated_last_line() else ""
            with self._path.open("a", encoding="utf-8") as log_file:
                log_file.write(separator + json.dumps(body) + "\n")
            self._last_hash = body["hash"]
            return body

    def read_events(self) -> list[dict[str, Any]]:
        events = []
        for line_number, line in self._read_lines():
            event = parse_event_line(line)
            if event is None:
                logger.warning(UNREADABLE_LINE_MESSAGE, line_number)
                continue
            events.append(event)
        return events

    def verify_chain(self) -> ChainVerification:
        previous_hash, checked = GENESIS_HASH, 0
        for line_number, line in self._read_lines():
            checked += 1
            event = parse_event_line(line)
            if event is None:
                return ChainVerification(False, checked, BROKEN_LINE_FORMAT.format(line_number=line_number), None)
            body = {key: value for key, value in event.items() if key != "hash"}
            if event.get("prev_hash") != previous_hash or compute_event_hash(previous_hash, body) != event.get("hash"):
                first_broken = event.get("event_id") or BROKEN_LINE_FORMAT.format(line_number=line_number)
                return ChainVerification(False, checked, first_broken, None)
            previous_hash = event["hash"]
        return ChainVerification(True, checked, None, previous_hash)

    def _has_unterminated_last_line(self) -> bool:
        if not self._path.exists() or self._path.stat().st_size == 0:
            return False
        with self._path.open("rb") as log_file:
            log_file.seek(-1, 2)
            return log_file.read(1) != b"\n"

    def _read_lines(self) -> list[tuple[int, str]]:
        if not self._path.exists():
            return []
        with self._path.open(encoding="utf-8") as log_file:
            return [(line_number, line) for line_number, line in enumerate(log_file, 1) if line.strip()]
