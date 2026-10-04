import secrets
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

STEP_ID_PREFIX = "step_"
TRACEPARENT_VERSION = "00"
TRACEPARENT_SAMPLED_FLAG = "01"
SPAN_ID_BYTES = 8


class TraceStepKind(StrEnum):
    USER_PROMPT = "user_prompt"
    CHECKPOINT = "checkpoint"
    GUARD = "guard"
    AGENT_TURN = "agent_turn"
    DATA_FETCH = "data_fetch"
    FETCH_STEP = "fetch_step"
    REPLY = "reply"


class StepOutcome(StrEnum):
    PASSED = "passed"
    DENIED = "denied"
    FAILED = "failed"
    INFO = "info"


@dataclass(frozen=True)
class TraceStep:
    step_id: str
    parent_step_id: str | None
    kind: TraceStepKind
    name: str
    outcome: StepOutcome
    reason: str
    detail: dict[str, Any]
    started_at: datetime
    duration_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "parent_step_id": self.parent_step_id,
            "kind": self.kind.value,
            "name": self.name,
            "outcome": self.outcome.value,
            "reason": self.reason,
            "detail": self.detail,
            "started_at": self.started_at.isoformat(),
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceStep":
        return cls(
            step_id=data["step_id"],
            parent_step_id=data.get("parent_step_id"),
            kind=TraceStepKind(data["kind"]),
            name=data["name"],
            outcome=StepOutcome(data["outcome"]),
            reason=data.get("reason", ""),
            detail=data.get("detail", {}),
            started_at=datetime.fromisoformat(data["started_at"]),
            duration_ms=data.get("duration_ms", 0.0),
        )


class TraceRecorder:
    def __init__(self) -> None:
        self._steps: list[TraceStep] = []

    @property
    def steps(self) -> tuple[TraceStep, ...]:
        return tuple(self._steps)

    def add_step(
        self,
        kind: TraceStepKind,
        name: str,
        outcome: StepOutcome,
        *,
        parent_step_id: str | None = None,
        reason: str = "",
        detail: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        duration_ms: float = 0.0,
    ) -> str:
        step_id = f"{STEP_ID_PREFIX}{len(self._steps) + 1}"
        self._steps.append(TraceStep(
            step_id=step_id,
            parent_step_id=parent_step_id,
            kind=kind,
            name=name,
            outcome=outcome,
            reason=reason,
            detail=detail or {},
            started_at=started_at or datetime.now(UTC),
            duration_ms=duration_ms,
        ))
        return step_id


def new_trace_id() -> str:
    return uuid.uuid4().hex


def new_span_id() -> str:
    return secrets.token_hex(SPAN_ID_BYTES)


def build_traceparent(trace_id: str, span_id: str) -> str:
    return f"{TRACEPARENT_VERSION}-{trace_id}-{span_id}-{TRACEPARENT_SAMPLED_FLAG}"


def order_steps_as_tree(steps: Sequence[TraceStep]) -> list[TraceStep]:
    steps_in_creation_order = sorted(steps, key=lambda step: int(step.step_id.removeprefix(STEP_ID_PREFIX)))
    children_by_parent: dict[str | None, list[TraceStep]] = defaultdict(list)
    for step in steps_in_creation_order:
        children_by_parent[step.parent_step_id].append(step)
    ordered: list[TraceStep] = []
    pending = list(reversed(children_by_parent[None]))
    while pending:
        step = pending.pop()
        ordered.append(step)
        pending.extend(reversed(children_by_parent[step.step_id]))
    return ordered
