import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, ClassVar, Self

from pydantic import BaseModel

from gateway.agent.chat_model import ChatModelReply, ToolCallRequest
from gateway.agent.tools import ToolCallContext, ToolCallResult, ToolDefinition
from gateway.guards.contract import GuardDecision, GuardDependencies, GuardVerdict
from gateway.guards.pipeline import ConfiguredGuard, GuardMode
from gateway.jev.client import JevNoulAnswers, JevUsage, NoulQuestion

ALLOW_VERDICT = GuardVerdict(GuardDecision.ALLOW, "fine")
REFUSE_VERDICT = GuardVerdict(GuardDecision.REFUSE, "bad")


class EmptySettings(BaseModel):
    pass


class ScriptedGuard:
    type_name: ClassVar[str] = "scripted"
    settings_model: ClassVar[type[BaseModel]] = EmptySettings

    def __init__(self, verdict: GuardVerdict = ALLOW_VERDICT, delay_seconds: float = 0.0, error: Exception | None = None):
        self.verdict = verdict
        self.delay_seconds = delay_seconds
        self.error = error
        self.checked_subjects: list[Any] = []

    @classmethod
    def create(cls, settings: BaseModel, dependencies: GuardDependencies) -> Self:
        return cls()

    async def check(self, subject: Any) -> GuardVerdict:
        self.checked_subjects.append(subject)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        return self.verdict


def build_configured_guard_for_test(
    guard: ScriptedGuard,
    instance_id: str = "g",
    mode: GuardMode = GuardMode.ENFORCE,
    timeout_seconds: float = 1.0,
    on_error: GuardDecision = GuardDecision.REFUSE,
) -> ConfiguredGuard:
    return ConfiguredGuard(instance_id, guard, mode, timeout_seconds, on_error)


class FakeJevClient:
    def __init__(self, probabilities: dict[str, float] | None = None, model: str = "jev-test", error: Exception | None = None):
        self.probabilities = probabilities or {}
        self.model = model
        self.error = error
        self.received_states: list[dict[str, Any]] = []
        self.received_questions: list[Mapping[str, NoulQuestion]] = []

    async def ask_nouls(self, state: dict[str, Any], questions: Mapping[str, NoulQuestion]) -> JevNoulAnswers:
        self.received_states.append(state)
        self.received_questions.append(questions)
        if self.error is not None:
            raise self.error
        return JevNoulAnswers(
            probabilities={key: self.probabilities[key] for key in questions if key in self.probabilities},
            model=self.model,
            usage=JevUsage(input_tokens=10, output_tokens=2),
        )


TEST_CHAT_MODEL_NAME = "fake-model"


def build_answer_reply(text: str) -> ChatModelReply:
    return ChatModelReply(text, (), TEST_CHAT_MODEL_NAME)


def build_tool_call_reply(*tool_names: str) -> ChatModelReply:
    calls = tuple(ToolCallRequest(f"call_{index}", name, {"n": index}) for index, name in enumerate(tool_names))
    return ChatModelReply("", calls, TEST_CHAT_MODEL_NAME)


class FakeChatModel:
    def __init__(self, replies: list[ChatModelReply | Exception]):
        self._replies = list(replies)
        self.received_messages: list[list[dict[str, Any]]] = []
        self.received_tools: list[list[ToolDefinition]] = []

    async def complete(self, messages: list[dict[str, Any]], tools: list[ToolDefinition]) -> ChatModelReply:
        self.received_messages.append([dict(message) for message in messages])
        self.received_tools.append(tools)
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeToolSession:
    def __init__(self, provider: "FakeToolProvider"):
        self._provider = provider

    async def list_tools(self) -> list[ToolDefinition]:
        return self._provider.tools

    async def call_tool(self, name: str, arguments: dict[str, Any], call_context: ToolCallContext) -> ToolCallResult:
        self._provider.calls.append((name, arguments, call_context))
        if self._provider.error is not None:
            raise self._provider.error
        return self._provider.results_by_name[name]


class FakeToolProvider:
    def __init__(
        self,
        tools: list[ToolDefinition] | None = None,
        results_by_name: dict[str, ToolCallResult] | None = None,
        error: Exception | None = None,
    ):
        self.tools = tools or [ToolDefinition("list_transactions", "List transactions", {"type": "object", "properties": {}})]
        self.results_by_name = results_by_name or {"list_transactions": ToolCallResult("[{\"id\": 1}]", False, ())}
        self.error = error
        self.calls: list[tuple[str, dict[str, Any], ToolCallContext]] = []

    @asynccontextmanager
    async def open_session(self) -> AsyncIterator[FakeToolSession]:
        yield FakeToolSession(self)
