from typing import Any

from gateway.audit.trace import StepOutcome, TraceStep, TraceStepKind
from gateway.core.reply import DeniedAt, ReplyOutcome
from gateway.identity.resolver import UserIdentity


def get_denied_at(outcome: ReplyOutcome, steps: tuple[TraceStep, ...]) -> DeniedAt | None:
    if outcome is ReplyOutcome.REFUSED:
        return DeniedAt.CHECKPOINT_1
    if any(step.kind is TraceStepKind.DATA_FETCH and step.outcome is StepOutcome.DENIED for step in steps):
        return DeniedAt.CHECKPOINT_2
    return None


def count_fetches(steps: tuple[TraceStep, ...], outcome: StepOutcome) -> int:
    return sum(1 for step in steps if step.kind is TraceStepKind.DATA_FETCH and step.outcome is outcome)


def build_audit_event(
    request_id: str,
    trace_id: str,
    user: UserIdentity,
    session_id: str,
    config_version: str,
    outcome: ReplyOutcome,
    reason: str,
    steps: tuple[TraceStep, ...],
    duration_ms: float,
) -> dict[str, Any]:
    denied_at = get_denied_at(outcome, steps)
    return {
        "request_id": request_id,
        "trace_id": trace_id,
        "user": {"id": user.user_id, "email": user.email, "name": user.name},
        "session_id": session_id,
        "config_version": config_version,
        "outcome": outcome.value,
        "reason": reason,
        "denied_at": None if denied_at is None else denied_at.value,
        "fetches": {
            "passed": count_fetches(steps, StepOutcome.PASSED),
            "denied": count_fetches(steps, StepOutcome.DENIED),
        },
        "duration_ms": duration_ms,
        "steps": [step.to_dict() for step in steps],
    }
