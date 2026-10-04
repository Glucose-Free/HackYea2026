import json
from pathlib import Path

from gateway.audit.log import AuditLog, classify_event


def test_append_links_events_and_chain_verifies(tmp_path: Path):
    audit_log = AuditLog(tmp_path / "audit.jsonl")
    first = audit_log.append({"request_id": "r1", "outcome": "answered"})
    second = audit_log.append({"request_id": "r2", "outcome": "refused", "denied_at": "checkpoint_1"})

    assert first["event_id"] == "evt_00001"
    assert second["prev_hash"] == first["hash"]
    verification = audit_log.verify_chain()
    assert verification.intact and verification.checked == 2 and verification.head == second["hash"]


def test_tampering_is_detected(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    audit_log = AuditLog(log_path)
    audit_log.append({"request_id": "r1", "outcome": "answered", "user": {"id": "alice"}})
    audit_log.append({"request_id": "r2", "outcome": "answered"})
    lines = log_path.read_text().splitlines()
    tampered = json.loads(lines[0])
    tampered["user"]["id"] = "mallory"
    log_path.write_text(json.dumps(tampered) + "\n" + lines[1] + "\n")

    verification = audit_log.verify_chain()
    assert not verification.intact
    assert verification.first_broken == "evt_00001"


def test_new_instance_continues_existing_chain(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    AuditLog(log_path).append({"outcome": "answered"})
    second = AuditLog(log_path).append({"outcome": "answered"})
    assert second["event_id"] == "evt_00002"
    assert AuditLog(log_path).verify_chain().intact


def test_corrupt_trailing_line_is_skipped_on_read_and_reported_by_verify(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    audit_log = AuditLog(log_path)
    audit_log.append({"request_id": "r1", "outcome": "answered"})
    with log_path.open("a") as log_file:
        log_file.write('{"request_id": "r2", "outco')

    reopened = AuditLog(log_path)
    assert [event["request_id"] for event in reopened.read_events()] == ["r1"]
    verification = reopened.verify_chain()
    assert not verification.intact
    assert verification.first_broken == "line 2"


def test_classification_reflects_outcome_and_denial_point():
    assert classify_event({"outcome": "answered"}).event_type == "ANSWERED"
    assert classify_event({"outcome": "answered", "denied_at": "checkpoint_2"}).event_type == "FETCH_DENIED"
    assert classify_event({"outcome": "refused", "denied_at": "checkpoint_1"}).event_type == "REFUSED"
    assert classify_event({"outcome": "failed_closed"}).severity == "medium"


def test_missing_file_reads_empty_and_verifies(tmp_path: Path):
    audit_log = AuditLog(tmp_path / "nested" / "audit.jsonl")
    assert audit_log.read_events() == []
    assert audit_log.verify_chain().intact


def test_log_whose_last_event_lacks_hash_still_opens_and_appends(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    log_path.write_text('{"request_id": "hand-edited", "outcome": "answered"}\n')
    audit_log = AuditLog(log_path)
    appended = audit_log.append({"request_id": "r2", "outcome": "answered"})
    assert appended["event_id"] == "evt_00002"
    assert not audit_log.verify_chain().intact


def test_append_after_truncated_tail_keeps_new_event_readable(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    audit_log = AuditLog(log_path)
    audit_log.append({"request_id": "r1", "outcome": "answered"})
    with log_path.open("a") as log_file:
        log_file.write('{"request_id": "partial", "outc')
    audit_log.append({"request_id": "r3", "outcome": "answered"})
    assert [event["request_id"] for event in AuditLog(log_path).read_events()] == ["r1", "r3"]


def test_non_object_lines_do_not_break_startup_or_verification(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    log_path.write_text("123\n")
    audit_log = AuditLog(log_path)
    assert audit_log.read_events() == []
    assert audit_log.append({"outcome": "answered"})["event_id"] == "evt_00001"
    verification = audit_log.verify_chain()
    assert not verification.intact and verification.first_broken == "line 1"
