import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from gateway.agent.tools import DENIED_TOOL_RESULT_PREFIX, ToolDefinition

CHAT_COMPLETIONS_PATH = "/chat/completions"
MALFORMED_REPLY_ERROR = "chat model reply has no choices[0].message"
MALFORMED_TOOL_CALL_ERROR = "chat model returned a malformed tool call: {error}"
NON_OBJECT_ARGUMENTS_ERROR = "tool call arguments must be a JSON object"

STUB_MODEL_NAME = "stub-chat-model"
STUB_DATA_REQUEST_KEYWORDS = ("show", "list", "get", "find", "how many", "give me", "fetch")
STUB_TOOL_NAME_STOPWORDS = {"get", "list", "show", "fetch"}
STUB_ENTITY_ID_PATTERN = re.compile(r"\b[A-Z]+-[0-9]+\b")
STUB_GREETING = "I'm the demo data assistant. Ask me for data, for example: show me recent transactions."
STUB_TOOL_RESULT_ANSWER = "Here is what the data service returned:\n{content}"
STUB_TOOL_DENIED_ANSWER = "The data service refused this request: {reason}"
TOOL_ROLE = "tool"
USER_ROLE = "user"


@dataclass(frozen=True)
class ToolCallRequest:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatModelReply:
    content: str
    tool_calls: tuple[ToolCallRequest, ...]
    model: str


class ChatModelError(RuntimeError):
    pass


class ChatModel(Protocol):
    async def complete(self, messages: list[dict[str, Any]], tools: list[ToolDefinition]) -> ChatModelReply: ...


class OpenAiCompatibleChatModel:
    def __init__(self, http_client: httpx.AsyncClient, base_url: str, model: str, api_key: str):
        self._http_client = http_client
        self._completions_url = base_url.rstrip("/") + CHAT_COMPLETIONS_PATH
        self._model = model
        self._api_key = api_key

    async def complete(self, messages: list[dict[str, Any]], tools: list[ToolDefinition]) -> ChatModelReply:
        payload: dict[str, Any] = {"model": self._model, "messages": messages}
        if tools:
            payload["tools"] = [build_openai_tool(tool) for tool in tools]
        response = await self._http_client.post(
            self._completions_url,
            json=payload,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        response.raise_for_status()
        return parse_openai_reply(response.json())


def build_openai_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": tool.input_schema}}


def parse_openai_reply(body: dict[str, Any]) -> ChatModelReply:
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as error:
        raise ChatModelError(MALFORMED_REPLY_ERROR) from error
    tool_calls = tuple(parse_tool_call(raw_call) for raw_call in message.get("tool_calls") or [])
    return ChatModelReply(message.get("content") or "", tool_calls, body.get("model", ""))


def parse_tool_call(raw_call: dict[str, Any]) -> ToolCallRequest:
    try:
        arguments = json.loads(raw_call["function"].get("arguments") or "{}")
        call = ToolCallRequest(raw_call["id"], raw_call["function"]["name"], arguments)
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ChatModelError(MALFORMED_TOOL_CALL_ERROR.format(error=error)) from error
    if not isinstance(arguments, dict):
        raise ChatModelError(NON_OBJECT_ARGUMENTS_ERROR)
    return call


class StubChatModel:
    """Deterministic stand-in so the demo runs end to end without a hosted model."""

    async def complete(self, messages: list[dict[str, Any]], tools: list[ToolDefinition]) -> ChatModelReply:
        last_message = messages[-1]
        if last_message["role"] == TOOL_ROLE:
            return ChatModelReply(build_stub_answer_from_tool_result(last_message["content"]), (), STUB_MODEL_NAME)
        latest_user_text = get_latest_user_text(messages)
        lowered_user_text = latest_user_text.lower()
        if tools and any(keyword in lowered_user_text for keyword in STUB_DATA_REQUEST_KEYWORDS):
            tool = choose_tool_for_message(lowered_user_text, tools)
            arguments = build_stub_arguments(latest_user_text, tool)
            call = ToolCallRequest(f"call_{uuid.uuid4().hex[:8]}", tool.name, arguments)
            return ChatModelReply("", (call,), STUB_MODEL_NAME)
        return ChatModelReply(STUB_GREETING, (), STUB_MODEL_NAME)


def build_stub_answer_from_tool_result(tool_content: str) -> str:
    if tool_content.startswith(DENIED_TOOL_RESULT_PREFIX):
        return STUB_TOOL_DENIED_ANSWER.format(reason=tool_content.removeprefix(DENIED_TOOL_RESULT_PREFIX))
    return STUB_TOOL_RESULT_ANSWER.format(content=tool_content)


def get_latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message["role"] == USER_ROLE:
            return str(message.get("content") or "")
    return ""


def build_stub_arguments(message_text: str, tool: ToolDefinition) -> dict[str, Any]:
    # A scripted model cannot understand the request; the first ID-shaped token (e.g. CUST-17) is enough for the demo.
    entity_id = STUB_ENTITY_ID_PATTERN.search(message_text)
    if entity_id is None:
        return {}
    return {name: entity_id.group() for name in tool.input_schema.get("required", [])}


def choose_tool_for_message(message_text: str, tools: list[ToolDefinition]) -> ToolDefinition:
    def count_matching_name_words(tool: ToolDefinition) -> int:
        name_words = set(tool.name.lower().split("_")) - STUB_TOOL_NAME_STOPWORDS
        return sum(1 for word in name_words if word in message_text)

    return max(tools, key=count_matching_name_words)
