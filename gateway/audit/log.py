import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64
EVENT_ID_FORMAT = "evt_{number:05d}"
BROKEN_LINE_FORMAT = "line {line_number}"
UNREADABLE_LINE_MESSAGE = "skipping unreadable audit log line %d"
UNREADABLE_ANCHOR_LOCATION = "anchor"
ANCHOR_MISMATCH_MESSAGE = (
    "audit log %s does not match its anchor (%d events, head %s); the anchor is kept as evidence and no longer advanced"
)
ANCHOR_TEMPORARY_SUFFIX = ".tmp"

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
class AuditAnchor:
    """The event count and head hash last written, kept apart from the log so a cut-off tail stays detectable."""

    count: int
    head: str


class UnreadableAnchorError(ValueError):
    pass


class AuditAnchorFile:
    """Anchor on a separate volume. It stops edits to the log file alone; whoever can write both can still forge
    both, so for stronger evidence copy the head from /audit/verify somewhere the gateway host cannot write."""

    def __init__(self, path: Path):
        self._path = path

    def read(self) -> AuditAnchor | None:
        if not self._path.exists():
            return None
        try:
            raw_anchor = json.loads(self._path.read_text(encoding="utf-8"))
            return AuditAnchor(int(raw_anchor["count"]), str(raw_anchor["head"]))
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise UnreadableAnchorError(str(self._path)) from error

    def write(self, anchor: AuditAnchor) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_name(self._path.name + ANCHOR_TEMPORARY_SUFFIX)
        with temporary_path.open("w", encoding="utf-8") as anchor_file:
            json.dump({"count": anchor.count, "head": anchor.head}, anchor_file)
            anchor_file.flush()
            os.fsync(anchor_file.fileno())
        os.replace(temporary_path, self._path)


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

    def __init__(self, path: Path, anchor_file: AuditAnchorFile | None = None):
        self._path = path
        self._lock = threading.Lock()
        events = self.read_events()
        # A hand-edited last line without a hash must not stop the gateway from starting; verify_chain reports it.
        self._last_hash = events[-1].get("hash", GENESIS_HASH) if events else GENESIS_HASH
        self._count = len(events)
        self._anchor_file = anchor_file
        self._anchor_frozen = self._open_anchor(events)

    def _open_anchor(self, events: list[dict[str, Any]]) -> bool:
        """Returns whether the anchor must stay as it is, because the log no longer matches it."""
        if self._anchor_file is None:
            return False
        try:
            anchor = self._anchor_file.read()
        except UnreadableAnchorError:
            logger.error(ANCHOR_MISMATCH_MESSAGE, self._path, -1, None)
            return True
        if anchor is None:
            # Trust on first use: a log written before anchoring existed (e.g. the demo seed) is adopted as it is.
            self._anchor_file.write(AuditAnchor(self._count, self._last_hash))
            return False
        if is_consistent_with_anchor(events, anchor):
            return False
        logger.error(ANCHOR_MISMATCH_MESSAGE, self._path, anchor.count, anchor.head)
        return True

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
            if self._anchor_file is not None and not self._anchor_frozen:
                self._anchor_file.write(AuditAnchor(self._count, self._last_hash))
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
        try:
            anchor = self._anchor_file.read() if self._anchor_file is not None else None
        except UnreadableAnchorError:
            return ChainVerification(False, 0, UNREADABLE_ANCHOR_LOCATION, None)
        previous_hash, checked, anchored_hash = GENESIS_HASH, 0, None
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
            if anchor is not None and checked == anchor.count:
                anchored_hash = previous_hash
        # The chain only proves the lines agree with each other; the anchor proves none were cut off or rehashed.
        if anchor is not None and anchor.count > 0 and anchored_hash != anchor.head:
            return ChainVerification(False, checked, EVENT_ID_FORMAT.format(number=anchor.count), None)
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


def is_consistent_with_anchor(events: list[dict[str, Any]], anchor: AuditAnchor) -> bool:
    if anchor.count == 0:
        return True
    return len(events) >= anchor.count and events[anchor.count - 1].get("hash") == anchor.head
