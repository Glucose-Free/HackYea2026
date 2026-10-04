import json

import httpx
import pytest

from gateway.agent.chat_model import (
    ChatModelError,
    OpenAiCompatibleChatModel,
    StubChatModel,
    parse_openai_reply,
)
from gateway.agent.tools import DENIED_TOOL_RESULT_PREFIX, ToolDefinition

TOOLS = [
    ToolDefinition("list_recent_transactions", "List transactions", {"type": "object", "properties": {}}),
    ToolDefinition("get_customer_phone_numbers", "Phones", {"type": "object", "properties": {}}),
]


async def test_openai_compatible_model_sends_tools_and_parses_tool_calls():
    captured_requests = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={
            "model": "gpt-test",
            "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "list_recent_transactions", "arguments": "{\"limit\": 3}"}},
            ]}}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request)) as http_client:
        chat_model = OpenAiCompatibleChatModel(http_client, "https://llm.example/v1/", "gpt-test", "secret")
        reply = await chat_model.complete([{"role": "user", "content": "hi"}], TOOLS)

    request = captured_requests[0]
    assert str(request.url) == "https://llm.example/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer secret"
    body = json.loads(request.content)
    assert body["tools"][0] == {"type": "function", "function": {
        "name": "list_recent_transactions", "description": "List transactions", "parameters": {"type": "object", "properties": {}},
    }}
    assert reply.content == ""
    assert reply.tool_calls[0].name == "list_recent_transactions"
    assert reply.tool_calls[0].arguments == {"limit": 3}


def test_parse_rejects_malformed_tool_arguments_and_bodies():
    with pytest.raises(ChatModelError):
        parse_openai_reply({"choices": [{"message": {"tool_calls": [
            {"id": "c", "function": {"name": "x", "arguments": "{not json"}},
        ]}}]})
    with pytest.raises(ChatModelError):
        parse_openai_reply({"choices": [{"message": {"tool_calls": [
            {"id": "c", "function": {"name": "x", "arguments": "[1, 2]"}},
        ]}}]})
    with pytest.raises(ChatModelError):
        parse_openai_reply({"error": "nope"})


async def test_openai_compatible_model_raises_on_http_error():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as http_client:
        chat_model = OpenAiCompatibleChatModel(http_client, "https://llm.example/v1", "m", "k")
        with pytest.raises(httpx.HTTPStatusError):
            await chat_model.complete([{"role": "user", "content": "hi"}], [])


async def test_stub_calls_best_matching_tool_for_data_request():
    reply = await StubChatModel().complete([{"role": "user", "content": "Show me the customer phone numbers"}], TOOLS)
    assert [call.name for call in reply.tool_calls] == ["get_customer_phone_numbers"]


CUSTOMER_TOOLS = [
    ToolDefinition("get_customer_contact", "Contact", {
        "type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"],
    }),
]


async def test_stub_fills_the_required_argument_with_the_id_named_in_the_message():
    reply = await StubChatModel().complete(
        [{"role": "user", "content": "Give me the customer contact for CUST-17"}], CUSTOMER_TOOLS)
    assert [call.arguments for call in reply.tool_calls] == [{"customer_id": "CUST-17"}]


async def test_stub_sends_no_arguments_when_the_message_names_no_id():
    reply = await StubChatModel().complete([{"role": "user", "content": "Show me the customer contact"}], CUSTOMER_TOOLS)
    assert [call.arguments for call in reply.tool_calls] == [{}]


async def test_stub_answers_from_tool_results_and_refusals():
    passed = await StubChatModel().complete([{"role": "tool", "tool_call_id": "c", "content": "[1]"}], TOOLS)
    denied = await StubChatModel().complete(
        [{"role": "tool", "tool_call_id": "c", "content": DENIED_TOOL_RESULT_PREFIX + "inference risk"}], TOOLS)
    assert "[1]" in passed.content and not passed.tool_calls
    assert "refused" in denied.content and "inference risk" in denied.content


async def test_stub_greets_when_no_data_requested():
    reply = await StubChatModel().complete([{"role": "user", "content": "hello there"}], TOOLS)
    assert reply.tool_calls == () and reply.content
