import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from deploy.demo_audit.seed_audit_log import DEMO_USERS, list_demo_requests, write_chained_events
from gateway.audit.log import AuditLog
from gateway.audit.query import JsonlAuditQuery, TimeBucket, TimeRange


def test_seeded_log_has_intact_chain_and_feeds_the_dashboard(tmp_path: Path):
    now = datetime.now(UTC)
    rng = random.Random(1)
    log_path = tmp_path / "audit.jsonl"
    write_chained_events(log_path, list_demo_requests(now, rng), rng)

    audit_log = AuditLog(log_path)
    assert audit_log.verify_chain().intact
    query = JsonlAuditQuery(audit_log)
    last_day = TimeRange(now - timedelta(hours=24), now + timedelta(minutes=1))
    assert {entry.user_id for entry in query.list_user_fetch_stats(last_day)} == {profile.identity.user_id for profile in DEMO_USERS}
    buckets = query.get_fetch_totals(last_day, TimeBucket.HOUR)
    assert sum(bucket.denied_at_checkpoint_1 for bucket in buckets) > 0
    assert sum(bucket.denied_at_checkpoint_2 for bucket in buckets) > 0
