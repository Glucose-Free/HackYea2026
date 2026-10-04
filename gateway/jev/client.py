import asyncio
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
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


class JevResponseError(RuntimeError):
    pass


GRANITE_GENERATE_PATH = "/api/generate"
GRANITE_YES_TOKEN = "yes"
GRANITE_TOP_LOGPROBS = 5
GRANITE_MISSING_LOGPROBS_ERROR = "Granite Guardian reply has no logprobs; the Ollama server is too old to return them"
# Ollama's template for this model only knows its built-in risk names and maps anything else to "harmful by
# common sense", so custom checks need the raw prompt. Wording follows that template.
GRANITE_PROMPT = (
    "<|start_of_role|>user<|end_of_role|>You are a safety agent trying to help determine whether the user message "
    "is harmful according to the safety risk definition below.\n\n"
    "<start_of_turn>\nUser Message: {message}\n<end_of_turn>\n\n"
    "Our safety risk definition is defined below:\n<start_of_risk_definition>\n* {risk_definition}\n"
    "<end_of_risk_definition>\n\n"
    "Is the user message harmful based on the risk definition? Your answer must be either 'Yes' or 'No'."
    "<|end_of_text|>\n<|start_of_role|>assistant<|end_of_role|>"
)
GRANITE_RISK_QUESTION = "The 'User' message is described by this question being answered yes: {instructions}"
GRANITE_RISK_TRUE = "Yes means: {criteria}."
GRANITE_RISK_FALSE = "Not this risk: {criteria}."


class GraniteGuardianJevClient:
    """Jev-style yes/no checks on IBM Granite Guardian served by a local Ollama, so no paid API is needed.

    It judges the latest user message only: the model's prompt has room for one message, not a conversation.
    """

    def __init__(self, http_client: httpx.AsyncClient, base_url: str, model: str):
        self._http_client = http_client
        self._generate_url = base_url.rstrip("/") + GRANITE_GENERATE_PATH
        self._model = model

    async def ask_nouls(self, state: dict[str, Any], questions: Mapping[str, NoulQuestion]) -> JevNoulAnswers:
        message = str(state.get(LATEST_USER_MESSAGE_STATE_KEY, ""))
        labels = list(questions)
        replies = await asyncio.gather(*(self._ask(message, questions[label]) for label in labels))
        return JevNoulAnswers(
            probabilities={label: get_granite_yes_probability(reply) for label, reply in zip(labels, replies, strict=True)},
            model=self._model,
            usage=JevUsage(
                input_tokens=sum(reply.get("prompt_eval_count", 0) for reply in replies),
                output_tokens=sum(reply.get("eval_count", 0) for reply in replies),
            ),
        )

    async def _ask(self, message: str, question: NoulQuestion) -> dict[str, Any]:
        response = await self._http_client.post(self._generate_url, json={
            "model": self._model,
            "prompt": GRANITE_PROMPT.format(message=message, risk_definition=build_granite_risk_definition(question)),
            "raw": True,
            "stream": False,
            "logprobs": True,
            "top_logprobs": GRANITE_TOP_LOGPROBS,
            "options": {"temperature": 0, "num_predict": 1},
        })
        response.raise_for_status()
        return response.json()


def build_granite_risk_definition(question: NoulQuestion) -> str:
    parts = [GRANITE_RISK_QUESTION.format(instructions=question.instructions)]
    if question.criteria_true:
        parts.append(GRANITE_RISK_TRUE.format(criteria=question.criteria_true))
    if question.criteria_false:
        parts.append(GRANITE_RISK_FALSE.format(criteria=question.criteria_false))
    return " ".join(parts)


def get_granite_yes_probability(reply: dict[str, Any]) -> float:
    try:
        top_logprobs = reply["logprobs"][0]["top_logprobs"]
    except (KeyError, IndexError, TypeError) as error:
        raise JevResponseError(GRANITE_MISSING_LOGPROBS_ERROR) from error
    # The tokenizer spells the answer several ways ("Yes", " yes"); together they are the probability of yes.
    return sum(math.exp(entry["logprob"]) for entry in top_logprobs if entry["token"].strip().lower() == GRANITE_YES_TOKEN)


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
