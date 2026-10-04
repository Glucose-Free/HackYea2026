import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

from gateway.guards.contract import Guard, GuardDecision, GuardVerdict

SubjectT = TypeVar("SubjectT")

GUARD_TIMEOUT_REASON = "guard timed out after {timeout_seconds}s"
GUARD_ERROR_REASON = "guard raised {error_type}"


class GuardMode(StrEnum):
    ENFORCE = "enforce"
    MONITOR = "monitor"


@dataclass(frozen=True)
class ConfiguredGuard:
    instance_id: str
    guard: Guard[Any]
    mode: GuardMode
    timeout_seconds: float
    on_error: GuardDecision


@dataclass(frozen=True)
class GuardRun:
    instance_id: str
    type_name: str
    mode: GuardMode
    verdict: GuardVerdict
    errored: bool
    started_at: datetime
    duration_ms: float

    @property
    def blocks(self) -> bool:
        return self.mode is GuardMode.ENFORCE and self.verdict.decision is GuardDecision.REFUSE


@dataclass(frozen=True)
class PipelineVerdict:
    decision: GuardDecision
    runs: tuple[GuardRun, ...]

    @property
    def refused(self) -> bool:
        return self.decision is GuardDecision.REFUSE


class GuardPipeline(Generic[SubjectT]):
    def __init__(self, guards: Sequence[ConfiguredGuard]):
        self._guards = tuple(guards)

    @property
    def guards(self) -> tuple[ConfiguredGuard, ...]:
        return self._guards

    async def evaluate(self, subject: SubjectT) -> PipelineVerdict:
        runs = await asyncio.gather(*(run_configured_guard(guard, subject) for guard in self._guards))
        return PipelineVerdict(combine_guard_runs(runs), tuple(runs))


def combine_guard_runs(runs: Sequence[GuardRun]) -> GuardDecision:
    return GuardDecision.REFUSE if any(run.blocks for run in runs) else GuardDecision.ALLOW


async def run_configured_guard(configured_guard: ConfiguredGuard, subject: Any) -> GuardRun:
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    errored = True
    try:
        verdict = await asyncio.wait_for(configured_guard.guard.check(subject), configured_guard.timeout_seconds)
        errored = False
    except TimeoutError:
        verdict = GuardVerdict(
            configured_guard.on_error,
            GUARD_TIMEOUT_REASON.format(timeout_seconds=configured_guard.timeout_seconds),
        )
    except Exception as error:
        verdict = GuardVerdict(configured_guard.on_error, GUARD_ERROR_REASON.format(error_type=type(error).__name__))
    return GuardRun(
        instance_id=configured_guard.instance_id,
        type_name=type(configured_guard.guard).type_name,
        mode=configured_guard.mode,
        verdict=verdict,
        errored=errored,
        started_at=started_at,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )
