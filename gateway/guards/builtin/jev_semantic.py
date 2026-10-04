from typing import Any, ClassVar, Self

from pydantic import BaseModel, Field, model_validator

from gateway.core.conversation import Conversation, Message
from gateway.guards.contract import (
    GuardDecision,
    GuardDependencies,
    GuardFinding,
    GuardVerdict,
    MissingGuardDependencyError,
)
from gateway.jev.client import (
    LATEST_USER_MESSAGE_STATE_KEY,
    RECENT_CONVERSATION_STATE_KEY,
    JevClient,
    JevNoulAnswers,
    NoulQuestion,
)

GUARD_TYPE_NAME = "jev_semantic"
NO_USER_MESSAGE_REASON = "no user message to check"
ALL_CHECKS_PASSED_REASON = "all semantic checks below threshold"
CHECK_REFUSED_REASON = "{label}: probability {probability:.2f} >= threshold {threshold:.2f}"
MISSING_JEV_CLIENT_ERROR = "jev_semantic guard requires a Jev client"
MISSING_ANSWER_ERROR = "Jev returned no answer for check {label!r}"
DUPLICATE_LABEL_ERROR = "check labels must be unique, duplicated: {labels}"


class JevCheck(BaseModel):
    label: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    criteria_true: str | None = None
    criteria_false: str | None = None
    # Jev's docs put "act automatically" above 0.9; refusing is the automatic action here.
    refuse_threshold: float = Field(0.9, ge=0.0, le=1.0)


class JevSemanticSettings(BaseModel):
    checks: list[JevCheck] = Field(min_length=1)
    history_window: int = Field(10, ge=0)

    @model_validator(mode="after")
    def reject_duplicate_labels(self) -> Self:
        labels = [check.label for check in self.checks]
        duplicated = sorted({label for label in labels if labels.count(label) > 1})
        if duplicated:
            raise ValueError(DUPLICATE_LABEL_ERROR.format(labels=duplicated))
        return self


class JevSemanticGuard:
    type_name: ClassVar[str] = GUARD_TYPE_NAME
    settings_model: ClassVar[type[BaseModel]] = JevSemanticSettings

    def __init__(self, jev_client: JevClient, settings: JevSemanticSettings):
        self._jev_client = jev_client
        self._settings = settings

    @classmethod
    def create(cls, settings: BaseModel, dependencies: GuardDependencies) -> Self:
        if dependencies.jev_client is None:
            raise MissingGuardDependencyError(MISSING_JEV_CLIENT_ERROR)
        return cls(dependencies.jev_client, JevSemanticSettings.model_validate(settings.model_dump()))

    async def check(self, conversation: Conversation) -> GuardVerdict:
        latest_user_message = conversation.get_latest_user_message()
        if latest_user_message is None:
            return GuardVerdict(GuardDecision.ALLOW, NO_USER_MESSAGE_REASON)
        answers = await self._jev_client.ask_nouls(
            build_jev_state(conversation, latest_user_message, self._settings.history_window),
            {check.label: build_noul_question(check) for check in self._settings.checks},
        )
        findings = tuple(GuardFinding(check.label, get_answer_probability(answers, check.label)) for check in self._settings.checks)
        return build_verdict(self._settings.checks, findings, answers)


def build_jev_state(conversation: Conversation, latest_user_message: Message, history_window: int) -> dict[str, Any]:
    # Questions target the latest message only; Open WebUI resends refused turns, so judging the
    # whole conversation would refuse every turn after the first refusal.
    return {
        LATEST_USER_MESSAGE_STATE_KEY: latest_user_message.content,
        RECENT_CONVERSATION_STATE_KEY: [
            {"role": message.role, "content": message.content}
            for message in conversation.get_context_before_latest_user_message(history_window)
        ],
    }


def build_noul_question(check: JevCheck) -> NoulQuestion:
    return NoulQuestion(check.instructions, check.criteria_true, check.criteria_false)


def get_answer_probability(answers: JevNoulAnswers, label: str) -> float:
    if label not in answers.probabilities:
        raise KeyError(MISSING_ANSWER_ERROR.format(label=label))
    return answers.probabilities[label]


def build_verdict(checks: list[JevCheck], findings: tuple[GuardFinding, ...], answers: JevNoulAnswers) -> GuardVerdict:
    detail = {
        "jev_model": answers.model,
        "jev_input_tokens": answers.usage.input_tokens,
        "jev_output_tokens": answers.usage.output_tokens,
    }
    for check, finding in zip(checks, findings, strict=True):
        if finding.score >= check.refuse_threshold:
            reason = CHECK_REFUSED_REASON.format(label=check.label, probability=finding.score, threshold=check.refuse_threshold)
            return GuardVerdict(GuardDecision.REFUSE, reason, findings, detail)
    return GuardVerdict(GuardDecision.ALLOW, ALL_CHECKS_PASSED_REASON, findings, detail)
