from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from typesafe_sdk import AsyncTypeSafeClient, Noul, NoulCriteria

LATEST_USER_MESSAGE_STATE_KEY = "latest_user_message"
RECENT_CONVERSATION_STATE_KEY = "recent_conversation"

STUB_MODEL_NAME = "jev-stub"
STUB_HIGH_PROBABILITY = 0.97
STUB_LOW_PROBABILITY = 0.02
STUB_SUSPICIOUS_PHRASES = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "ignore your instructions",
    "system prompt",
    "you are now",
    "developer mode",
    "jailbreak",
)


@dataclass(frozen=True)
class NoulQuestion:
    instructions: str
    criteria_true: str | None = None
    criteria_false: str | None = None


@dataclass(frozen=True)
class JevUsage:
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class JevNoulAnswers:
    probabilities: dict[str, float]
    model: str
    usage: JevUsage


class JevClient(Protocol):
    async def ask_nouls(self, state: dict[str, Any], questions: Mapping[str, NoulQuestion]) -> JevNoulAnswers: ...


class TypeSafeJevClient:
    def __init__(self, sdk_client: AsyncTypeSafeClient, model: str):
        self._sdk_client = sdk_client
        self._model = model

    async def ask_nouls(self, state: dict[str, Any], questions: Mapping[str, NoulQuestion]) -> JevNoulAnswers:
        response = await self._sdk_client.system_one(
            state=state,
            questions={key: build_sdk_noul(question) for key, question in questions.items()},
            model=self._model,
        )
        return JevNoulAnswers(
            probabilities={key: answer.noul for key, answer in response.nouls.items()},
            model=response.model,
            usage=JevUsage(response.usage.input_tokens, response.usage.output_tokens),
        )


def build_sdk_noul(question: NoulQuestion) -> Noul:
    criteria = None
    if question.criteria_true is not None or question.criteria_false is not None:
        criteria = NoulCriteria(true=question.criteria_true, false=question.criteria_false)
    return Noul(instructions=question.instructions, criteria=criteria)


class StubJevClient:
    """Keyword heuristic standing in for Jev until an API key is available."""

    async def ask_nouls(self, state: dict[str, Any], questions: Mapping[str, NoulQuestion]) -> JevNoulAnswers:
        latest_user_message = str(state.get(LATEST_USER_MESSAGE_STATE_KEY, "")).lower()
        is_suspicious = any(phrase in latest_user_message for phrase in STUB_SUSPICIOUS_PHRASES)
        probability = STUB_HIGH_PROBABILITY if is_suspicious else STUB_LOW_PROBABILITY
        return JevNoulAnswers(
            probabilities={key: probability for key in questions},
            model=STUB_MODEL_NAME,
            usage=JevUsage(input_tokens=None, output_tokens=None),
        )
