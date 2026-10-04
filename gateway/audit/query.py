import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from gateway.audit.log import AuditLog
from gateway.audit.trace import StepOutcome, TraceStep, TraceStepKind, order_steps_as_tree
from gateway.core.reply import DeniedAt, ReplyOutcome

MAX_BUCKETS = 2000
DEFAULT_TIME_SPAN = timedelta(hours=24)
DEFAULT_PAGE_LIMIT = 50
BUCKET_DURATIONS = {"hour": timedelta(hours=1), "day": timedelta(days=1)}
INVERTED_RANGE_ERROR = "start must be before end"
TOO_MANY_BUCKETS_ERROR = "range would produce {count} buckets; the maximum is {maximum}"
INVALID_CURSOR_ERROR = "cursor {cursor!r} is not valid"
UNREADABLE_EVENT_MESSAGE = "skipping unreadable audit event %s"
# Written by Gateway on the user_prompt step; it is what the user asked the assistant to do.
PROMPT_DETAIL_KEY = "message"

logger = logging.getLogger(__name__)


class TimeBucket(StrEnum):
    HOUR = "hour"
    DAY = "day"


class RequestStatus(StrEnum):
    """How much of what the user asked for got through, as the dashboard's donut shows it."""

    PASSED = "passed"
    PARTIALLY_PASSED = "partially_passed"
    BLOCKED = "blocked"


class InvalidTimeRangeError(ValueError):
    pass


class InvalidCursorError(ValueError):
    pass


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class FetchAttempt:
    request_id: str
    user_id: str
    occurred_at: datetime
    passed: bool
    denied_at: DeniedAt | None


@dataclass(frozen=True)
class FetchTotalsBucket:
    bucket_start: datetime
    passed: int
    denied_at_checkpoint_1: int
    denied_at_checkpoint_2: int

    @property
    def denied(self) -> int:
        return self.denied_at_checkpoint_1 + self.denied_at_checkpoint_2


@dataclass(frozen=True)
class UserFetchStats:
    user_id: str
    user_email: str
    user_name: str
    passed: int
    denied: int
    last_fetch_at: datetime


@dataclass(frozen=True)
class RequestSummary:
    request_id: str
    user_id: str
    user_name: str
    occurred_at: datetime
    outcome: ReplyOutcome
    denied_at: DeniedAt | None
    status: RequestStatus
    prompt: str
    reason: str
    passed_fetches: int
    denied_fetches: int


@dataclass(frozen=True)
class RequestFilters:
    user_id: str | None = None
    outcome: ReplyOutcome | None = None
    denied_at: DeniedAt | None = None
    status: RequestStatus | None = None
    time_range: TimeRange | None = None
    only_problems: bool = False


@dataclass(frozen=True)
class PageRequest:
    cursor: str | None = None
    limit: int = DEFAULT_PAGE_LIMIT


@dataclass(frozen=True)
class RequestPage:
    items: list[RequestSummary]
    next_cursor: str | None


@dataclass(frozen=True)
class RequestStatusCounts:
    passed: int
    partially_passed: int
    blocked: int


@dataclass(frozen=True)
class RequestTrace:
    summary: RequestSummary
    steps: list[TraceStep]


class AuditQuery(Protocol):
    def get_fetch_totals(self, time_range: TimeRange, bucket: TimeBucket, *, user_id: str | None = None) -> list[FetchTotalsBucket]: ...
    def list_user_fetch_stats(self, time_range: TimeRange) -> list[UserFetchStats]: ...
    def list_requests(self, filters: RequestFilters, page: PageRequest) -> RequestPage: ...
    def get_request_status_counts(self, time_range: TimeRange) -> RequestStatusCounts: ...
    def get_request_trace(self, request_id: str) -> RequestTrace | None: ...


def ensure_utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def build_time_range(
    start: datetime | None,
    end: datetime | None,
    now: datetime | None = None,
    default_span: timedelta = DEFAULT_TIME_SPAN,
) -> TimeRange:
    resolved_end = ensure_utc(end) if end is not None else (now or datetime.now(UTC))
    resolved_start = ensure_utc(start) if start is not None else resolved_end - default_span
    if resolved_start >= resolved_end:
        raise InvalidTimeRangeError(INVERTED_RANGE_ERROR)
    return TimeRange(resolved_start, resolved_end)


def floor_to_bucket(moment: datetime, bucket: TimeBucket) -> datetime:
    floored = moment.replace(minute=0, second=0, microsecond=0)
    return floored.replace(hour=0) if bucket is TimeBucket.DAY else floored


def build_bucket_starts(time_range: TimeRange, bucket: TimeBucket) -> list[datetime]:
    step = BUCKET_DURATIONS[bucket.value]
    first = floor_to_bucket(time_range.start, bucket)
    count = int((time_range.end - first) / step) + 1
    if count > MAX_BUCKETS:
        raise InvalidTimeRangeError(TOO_MANY_BUCKETS_ERROR.format(count=count, maximum=MAX_BUCKETS))
    return [first + step * index for index in range(count) if first + step * index < time_range.end]


def get_event_time(event: dict[str, Any]) -> datetime:
    return ensure_utc(datetime.fromisoformat(event["ts"]))


def get_event_denied_at(event: dict[str, Any]) -> DeniedAt | None:
    return DeniedAt(event["denied_at"]) if event.get("denied_at") else None


def extract_fetch_attempts(event: dict[str, Any]) -> list[FetchAttempt]:
    request_id, user_id = event.get("request_id", ""), event.get("user", {}).get("id", "")
    if get_event_denied_at(event) is DeniedAt.CHECKPOINT_1:
        # A prompt refused at checkpoint 1 never reaches the MCP server; it counts as one denied attempt.
        return [FetchAttempt(request_id, user_id, get_event_time(event), False, DeniedAt.CHECKPOINT_1)]
    attempts = []
    for raw_step in event.get("steps", []):
        if raw_step.get("kind") != TraceStepKind.DATA_FETCH or raw_step.get("outcome") not in (StepOutcome.PASSED, StepOutcome.DENIED):
            continue
        passed = raw_step["outcome"] == StepOutcome.PASSED
        occurred_at = ensure_utc(datetime.fromisoformat(raw_step["started_at"]))
        attempts.append(FetchAttempt(request_id, user_id, occurred_at, passed, None if passed else DeniedAt.CHECKPOINT_2))
    return attempts


# Failed-closed requests count as blocked: the user got no answer, even though no guard denied anything.
def get_request_status(outcome: ReplyOutcome, denied_at: DeniedAt | None) -> RequestStatus:
    if outcome is not ReplyOutcome.ANSWERED:
        return RequestStatus.BLOCKED
    if denied_at is DeniedAt.CHECKPOINT_2:
        return RequestStatus.PARTIALLY_PASSED
    return RequestStatus.PASSED


def get_event_prompt(event: dict[str, Any]) -> str:
    for raw_step in event.get("steps", []):
        if raw_step.get("kind") == TraceStepKind.USER_PROMPT:
            return (raw_step.get("detail") or {}).get(PROMPT_DETAIL_KEY, "")
    return ""


def build_request_summary(event: dict[str, Any]) -> RequestSummary:
    user = event.get("user", {})
    fetches = event.get("fetches", {})
    outcome = ReplyOutcome(event["outcome"])
    denied_at = get_event_denied_at(event)
    return RequestSummary(
        request_id=event.get("request_id", ""),
        user_id=user.get("id", ""),
        user_name=user.get("name", ""),
        occurred_at=get_event_time(event),
        outcome=outcome,
        denied_at=denied_at,
        status=get_request_status(outcome, denied_at),
        prompt=get_event_prompt(event),
        reason=event.get("reason", ""),
        passed_fetches=fetches.get("passed", 0),
        denied_fetches=fetches.get("denied", 0),
    )


def matches_filters(summary: RequestSummary, filters: RequestFilters) -> bool:
    if filters.user_id is not None and summary.user_id != filters.user_id:
        return False
    if filters.outcome is not None and summary.outcome is not filters.outcome:
        return False
    if filters.denied_at is not None and summary.denied_at is not filters.denied_at:
        return False
    if filters.status is not None and summary.status is not filters.status:
        return False
    if filters.only_problems and summary.denied_at is None and summary.outcome is not ReplyOutcome.FAILED_CLOSED:
        return False
    if filters.time_range is not None and not (filters.time_range.start <= summary.occurred_at < filters.time_range.end):
        return False
    return True


def is_readable_event(event: dict[str, Any]) -> bool:
    # Hand-edited or foreign lines must not turn the dashboard into 500s; unreadable events are left out.
    try:
        ReplyOutcome(event["outcome"])
        get_event_denied_at(event)
        get_event_time(event)
        for raw_step in event.get("steps", []):
            TraceStep.from_dict(raw_step)
    except (KeyError, ValueError, TypeError, AttributeError):
        logger.warning(UNREADABLE_EVENT_MESSAGE, event.get("event_id"))
        return False
    return True


def parse_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.isdigit():
        raise InvalidCursorError(INVALID_CURSOR_ERROR.format(cursor=cursor))
    return int(cursor)


class JsonlAuditQuery:
    """Reads the whole log per query; fine at demo scale, replaced by a DB projection in production."""

    def __init__(self, audit_log: AuditLog):
        self._audit_log = audit_log

    def get_fetch_totals(self, time_range: TimeRange, bucket: TimeBucket, *, user_id: str | None = None) -> list[FetchTotalsBucket]:
        bucket_starts = build_bucket_starts(time_range, bucket)
        counts: dict[datetime, list[int]] = {bucket_start: [0, 0, 0] for bucket_start in bucket_starts}
        for attempt in self._list_fetch_attempts(time_range):
            if user_id is not None and attempt.user_id != user_id:
                continue
            bucket_counts = counts[floor_to_bucket(attempt.occurred_at, bucket)]
            bucket_counts[get_attempt_column(attempt)] += 1
        return [FetchTotalsBucket(bucket_start, *counts[bucket_start]) for bucket_start in bucket_starts]

    def list_user_fetch_stats(self, time_range: TimeRange) -> list[UserFetchStats]:
        attempts_by_user: dict[str, list[FetchAttempt]] = defaultdict(list)
        for attempt in self._list_fetch_attempts(time_range):
            attempts_by_user[attempt.user_id].append(attempt)
        users = self._get_latest_user_details()
        stats = [
            UserFetchStats(
                user_id=user_id,
                user_email=users.get(user_id, {}).get("email", ""),
                user_name=users.get(user_id, {}).get("name", ""),
                passed=sum(1 for attempt in attempts if attempt.passed),
                denied=sum(1 for attempt in attempts if not attempt.passed),
                last_fetch_at=max(attempt.occurred_at for attempt in attempts),
            )
            for user_id, attempts in attempts_by_user.items()
        ]
        return sorted(stats, key=lambda entry: (-entry.denied, entry.user_id))

    def list_requests(self, filters: RequestFilters, page: PageRequest) -> RequestPage:
        offset = parse_cursor(page.cursor)
        summaries = [summary for summary in self._list_summaries_newest_first() if matches_filters(summary, filters)]
        items = summaries[offset:offset + page.limit]
        next_offset = offset + page.limit
        return RequestPage(items, str(next_offset) if next_offset < len(summaries) else None)

    def get_request_status_counts(self, time_range: TimeRange) -> RequestStatusCounts:
        filters = RequestFilters(time_range=time_range)
        statuses = [summary.status for summary in self._list_summaries_newest_first() if matches_filters(summary, filters)]
        return RequestStatusCounts(
            passed=statuses.count(RequestStatus.PASSED),
            partially_passed=statuses.count(RequestStatus.PARTIALLY_PASSED),
            blocked=statuses.count(RequestStatus.BLOCKED),
        )

    def get_request_trace(self, request_id: str) -> RequestTrace | None:
        for event in self._read_valid_events():
            if event.get("request_id") == request_id:
                steps = [TraceStep.from_dict(raw_step) for raw_step in event.get("steps", [])]
                return RequestTrace(build_request_summary(event), order_steps_as_tree(steps))
        return None

    def _read_valid_events(self) -> Iterable[dict[str, Any]]:
        return (event for event in self._audit_log.read_events() if is_readable_event(event))

    def _list_fetch_attempts(self, time_range: TimeRange) -> list[FetchAttempt]:
        return [
            attempt
            for event in self._read_valid_events()
            for attempt in extract_fetch_attempts(event)
            if time_range.start <= attempt.occurred_at < time_range.end
        ]

    def _list_summaries_newest_first(self) -> list[RequestSummary]:
        summaries = [build_request_summary(event) for event in self._read_valid_events()]
        return sorted(summaries, key=lambda summary: summary.occurred_at, reverse=True)

    def _get_latest_user_details(self) -> dict[str, dict[str, str]]:
        return {event["user"]["id"]: event["user"] for event in self._read_valid_events() if event.get("user", {}).get("id")}


PASSED_COLUMN, CHECKPOINT_1_COLUMN, CHECKPOINT_2_COLUMN = 0, 1, 2


def get_attempt_column(attempt: FetchAttempt) -> int:
    if attempt.passed:
        return PASSED_COLUMN
    return CHECKPOINT_1_COLUMN if attempt.denied_at is DeniedAt.CHECKPOINT_1 else CHECKPOINT_2_COLUMN
