import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gateway.audit.log import AuditLog
from gateway.audit.query import (
    InvalidCursorError,
    InvalidTimeRangeError,
    JsonlAuditQuery,
    PageRequest,
    RequestFilters,
    RequestStatus,
    RequestStatusCounts,
    TimeBucket,
    TimeRange,
    build_time_range,
)
from gateway.audit.trace import StepOutcome, TraceRecorder, TraceStepKind
from gateway.core.reply import DeniedAt, ReplyOutcome

NOW = datetime(2026, 10, 4, 12, 30, tzinfo=UTC)


def build_event(
    request_id: str, user_id: str, outcome: str, denied_at: str | None, fetch_outcomes: list[StepOutcome], at: datetime, prompt: str = "",
) -> dict:
    recorder = TraceRecorder()
    root = recorder.add_step(TraceStepKind.USER_PROMPT, user_id, StepOutcome.INFO, detail={"message": prompt}, started_at=at)
    turn = recorder.add_step(TraceStepKind.AGENT_TURN, "model call 1", StepOutcome.INFO, parent_step_id=root, started_at=at)
    for index, fetch_outcome in enumerate(fetch_outcomes):
        fetch = recorder.add_step(TraceStepKind.DATA_FETCH, f"tool_{index}", fetch_outcome, parent_step_id=turn, started_at=at)
        if fetch_outcome is StepOutcome.DENIED:
            recorder.add_step(TraceStepKind.FETCH_STEP, "inference_guard", StepOutcome.DENIED, parent_step_id=fetch, started_at=at)
    recorder.add_step(TraceStepKind.REPLY, outcome, StepOutcome.PASSED, parent_step_id=root, started_at=at)
    return {
        "request_id": request_id, "user": {"id": user_id, "email": f"{user_id}@demo.local", "name": user_id.title()},
        "outcome": outcome, "reason": "", "denied_at": denied_at,
        "fetches": {"passed": fetch_outcomes.count(StepOutcome.PASSED), "denied": fetch_outcomes.count(StepOutcome.DENIED)},
        "steps": [step.to_dict() for step in recorder.steps],
    }


@pytest.fixture
def audit_query(tmp_path: Path) -> JsonlAuditQuery:
    events = [
        (build_event("r1", "alice", "answered", None, [StepOutcome.PASSED, StepOutcome.PASSED], NOW - timedelta(hours=2)), NOW - timedelta(hours=2)),
        (build_event("r2", "alice", "answered", "checkpoint_2", [StepOutcome.DENIED], NOW - timedelta(minutes=20)), NOW - timedelta(minutes=20)),
        (build_event("r3", "bob", "refused", "checkpoint_1", [], NOW - timedelta(minutes=10)), NOW - timedelta(minutes=10)),
        (build_event("r4", "carol", "answered", None, [], NOW - timedelta(minutes=5)), NOW - timedelta(minutes=5)),
    ]
    # Written directly with fixed timestamps; the hash chain is not under test here.
    log_path = tmp_path / "audit.jsonl"
    log_path.write_text("".join(json.dumps({**event, "ts": logged_at.isoformat()}) + "\n" for event, logged_at in events))
    return JsonlAuditQuery(AuditLog(log_path))


def test_fetch_totals_bucket_by_hour_with_zero_filled_gaps(audit_query: JsonlAuditQuery):
    time_range = TimeRange(NOW - timedelta(hours=3), NOW)
    buckets = audit_query.get_fetch_totals(time_range, TimeBucket.HOUR)
    assert [bucket.bucket_start.hour for bucket in buckets] == [9, 10, 11, 12]
    by_hour = {bucket.bucket_start.hour: bucket for bucket in buckets}
    assert (by_hour[10].passed, by_hour[10].denied) == (2, 0)
    assert (by_hour[12].passed, by_hour[12].denied_at_checkpoint_1, by_hour[12].denied_at_checkpoint_2) == (0, 1, 1)
    assert by_hour[9].passed == by_hour[9].denied == 0



def test_fetch_totals_for_one_user_exclude_other_users(audit_query: JsonlAuditQuery):
    buckets = audit_query.get_fetch_totals(TimeRange(NOW - timedelta(hours=3), NOW), TimeBucket.HOUR, user_id="bob")
    assert sum(bucket.passed for bucket in buckets) == 0
    assert sum(bucket.denied_at_checkpoint_1 for bucket in buckets) == 1
    assert sum(bucket.denied_at_checkpoint_2 for bucket in buckets) == 0

def test_user_stats_list_only_users_with_fetch_attempts(audit_query: JsonlAuditQuery):
    stats = audit_query.list_user_fetch_stats(TimeRange(NOW - timedelta(days=1), NOW))
    by_user = {entry.user_id: entry for entry in stats}
    assert set(by_user) == {"alice", "bob"}
    assert (by_user["alice"].passed, by_user["alice"].denied) == (2, 1)
    assert (by_user["bob"].passed, by_user["bob"].denied) == (0, 1)
    assert by_user["alice"].user_email == "alice@demo.local"


def test_list_requests_newest_first_with_filters_and_cursor(audit_query: JsonlAuditQuery):
    first_page = audit_query.list_requests(RequestFilters(), PageRequest(limit=2))
    assert [item.request_id for item in first_page.items] == ["r4", "r3"]
    second_page = audit_query.list_requests(RequestFilters(), PageRequest(cursor=first_page.next_cursor, limit=2))
    assert [item.request_id for item in second_page.items] == ["r2", "r1"]
    assert second_page.next_cursor is None

    denied = audit_query.list_requests(RequestFilters(denied_at=DeniedAt.CHECKPOINT_2), PageRequest())
    assert [item.request_id for item in denied.items] == ["r2"]
    refused = audit_query.list_requests(RequestFilters(outcome=ReplyOutcome.REFUSED, user_id="bob"), PageRequest())
    assert [item.request_id for item in refused.items] == ["r3"]


def test_request_status_says_how_much_of_the_request_got_through(tmp_path: Path):
    events = [
        build_event("passed", "alice", "answered", None, [StepOutcome.PASSED], NOW),
        build_event("partial", "alice", "answered", "checkpoint_2", [StepOutcome.PASSED, StepOutcome.DENIED], NOW),
        build_event("all-fetches-denied", "alice", "answered", "checkpoint_2", [StepOutcome.DENIED], NOW),
        build_event("refused", "bob", "refused", "checkpoint_1", [], NOW),
        build_event("failed", "bob", "failed_closed", None, [], NOW),
    ]
    log_path = tmp_path / "audit.jsonl"
    log_path.write_text("".join(json.dumps({**event, "ts": NOW.isoformat()}) + "\n" for event in events))
    items = JsonlAuditQuery(AuditLog(log_path)).list_requests(RequestFilters(), PageRequest()).items
    assert {item.request_id: item.status for item in items} == {
        "passed": RequestStatus.PASSED,
        "partial": RequestStatus.PARTIALLY_PASSED,
        "all-fetches-denied": RequestStatus.PARTIALLY_PASSED,
        "refused": RequestStatus.BLOCKED,
        "failed": RequestStatus.BLOCKED,
    }


def test_requests_filtered_by_status(audit_query: JsonlAuditQuery):
    partial = audit_query.list_requests(RequestFilters(status=RequestStatus.PARTIALLY_PASSED), PageRequest())
    assert [item.request_id for item in partial.items] == ["r2"]
    passed = audit_query.list_requests(RequestFilters(status=RequestStatus.PASSED), PageRequest())
    assert [item.request_id for item in passed.items] == ["r4", "r1"]


def test_status_counts_cover_only_the_time_range(audit_query: JsonlAuditQuery):
    last_hour = TimeRange(NOW - timedelta(hours=1), NOW)
    assert audit_query.get_request_status_counts(last_hour) == RequestStatusCounts(passed=1, partially_passed=1, blocked=1)


def test_summary_carries_the_user_prompt_and_tolerates_a_missing_one(tmp_path: Path):
    with_prompt = build_event("with", "alice", "answered", None, [], NOW, prompt="list my transactions")
    without_prompt = build_event("without", "alice", "answered", None, [], NOW)
    without_prompt["steps"] = [step for step in without_prompt["steps"] if step["kind"] != TraceStepKind.USER_PROMPT]
    log_path = tmp_path / "audit.jsonl"
    log_path.write_text("".join(json.dumps({**event, "ts": NOW.isoformat()}) + "\n" for event in [with_prompt, without_prompt]))
    items = JsonlAuditQuery(AuditLog(log_path)).list_requests(RequestFilters(), PageRequest()).items
    assert {item.request_id: item.prompt for item in items} == {"with": "list my transactions", "without": ""}


def test_invalid_cursor_rejected(audit_query: JsonlAuditQuery):
    with pytest.raises(InvalidCursorError):
        audit_query.list_requests(RequestFilters(), PageRequest(cursor="abc"))


def test_trace_is_in_tree_order_and_unknown_is_none(audit_query: JsonlAuditQuery):
    trace = audit_query.get_request_trace("r2")
    assert [step.kind for step in trace.steps] == [
        TraceStepKind.USER_PROMPT, TraceStepKind.AGENT_TURN, TraceStepKind.DATA_FETCH, TraceStepKind.FETCH_STEP, TraceStepKind.REPLY,
    ]
    assert trace.summary.denied_fetches == 1
    assert audit_query.get_request_trace("nope") is None


def test_time_range_validation():
    naive = build_time_range(datetime(2026, 10, 4, 10), datetime(2026, 10, 4, 11))
    assert naive.start.tzinfo is UTC
    defaulted = build_time_range(None, None, now=NOW)
    assert defaulted == TimeRange(NOW - timedelta(hours=24), NOW)
    with pytest.raises(InvalidTimeRangeError):
        build_time_range(NOW, NOW)


def test_too_many_buckets_rejected(audit_query: JsonlAuditQuery):
    with pytest.raises(InvalidTimeRangeError):
        audit_query.get_fetch_totals(TimeRange(NOW - timedelta(days=365), NOW), TimeBucket.HOUR)


def test_corrupt_line_is_ignored(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    log_path.write_text('{"request_id": "x"\n')
    query = JsonlAuditQuery(AuditLog(log_path))
    assert query.list_requests(RequestFilters(), PageRequest()).items == []


def test_events_with_unknown_outcome_or_bad_timestamps_are_skipped(tmp_path: Path):
    good = build_event("ok", "alice", "answered", None, [StepOutcome.PASSED], NOW - timedelta(minutes=1))
    bad_outcome = {**build_event("bad1", "alice", "answered", None, [], NOW), "outcome": "weird"}
    bad_ts = build_event("bad2", "alice", "answered", None, [], NOW)
    bad_step_time = build_event("bad3", "alice", "answered", None, [StepOutcome.PASSED], NOW)
    bad_step_time["steps"][2]["started_at"] = "yesterday"
    log_path = tmp_path / "audit.jsonl"
    lines = [
        {**good, "ts": NOW.isoformat()},
        {**bad_outcome, "ts": NOW.isoformat()},
        {**bad_ts, "ts": "not a time"},
        {**bad_step_time, "ts": NOW.isoformat()},
    ]
    log_path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    query = JsonlAuditQuery(AuditLog(log_path))
    time_range = TimeRange(NOW - timedelta(hours=1), NOW + timedelta(hours=1))

    assert [item.request_id for item in query.list_requests(RequestFilters(), PageRequest()).items] == ["ok"]
    assert sum(bucket.passed for bucket in query.get_fetch_totals(time_range, TimeBucket.HOUR)) == 1
    assert query.get_request_trace("bad3") is None


def test_problem_filter_finds_denials_older_than_many_answered_requests(tmp_path: Path):
    denied = build_event("denied", "bob", "refused", "checkpoint_1", [], NOW - timedelta(hours=1))
    lines = [{**denied, "ts": (NOW - timedelta(hours=1)).isoformat()}]
    for index in range(150):
        answered = build_event(f"a{index}", "alice", "answered", None, [], NOW)
        lines.append({**answered, "ts": (NOW - timedelta(minutes=30) + timedelta(seconds=index)).isoformat()})
    log_path = tmp_path / "audit.jsonl"
    log_path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    page = JsonlAuditQuery(AuditLog(log_path)).list_requests(RequestFilters(only_problems=True), PageRequest(limit=20))
    assert [item.request_id for item in page.items] == ["denied"]
