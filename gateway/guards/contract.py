from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Protocol, Self, TypeVar

from pydantic import BaseModel

from gateway.jev.client import JevClient

SubjectT = TypeVar("SubjectT", contravariant=True)


class GuardDecision(StrEnum):
    ALLOW = "allow"
    REFUSE = "refuse"


@dataclass(frozen=True)
class GuardFinding:
    label: str
    score: float


@dataclass(frozen=True)
class GuardVerdict:
    decision: GuardDecision
    reason: str
    findings: tuple[GuardFinding, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GuardDependencies:
    jev_client: JevClient | None = None


class MissingGuardDependencyError(RuntimeError):
    pass


class Guard(Protocol[SubjectT]):
    type_name: ClassVar[str]
    settings_model: ClassVar[type[BaseModel]]

    @classmethod
    def create(cls, settings: BaseModel, dependencies: GuardDependencies) -> Self: ...

    async def check(self, subject: SubjectT) -> GuardVerdict: ...
