import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent

CHECKPOINT_STEPS_FIELD = "checkpoint_steps"
TRACEPARENT_META_KEY = "traceparent"
USER_ID_META_KEY = "ai-control-gateway/user_id"
SESSION_ID_META_KEY = "ai-control-gateway/session_id"
DENIED_TOOL_RESULT_PREFIX = "Request denied by the data service: "


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class CheckpointStep:
    middleware: str
    outcome: str
    reason: str


@dataclass(frozen=True)
class ToolCaller:
    """Who the gateway fetches for; checkpoint 2 keys its inference state on this."""

    user_id: str
    session_id: str


@dataclass(frozen=True)
class ToolCallContext:
    traceparent: str
    caller: ToolCaller


@dataclass(frozen=True)
class ToolCallResult:
    content: str
    denied: bool
    checkpoint_steps: tuple[CheckpointStep, ...]


class ToolSession(Protocol):
    async def list_tools(self) -> list[ToolDefinition]: ...
    async def call_tool(self, name: str, arguments: dict[str, Any], call_context: ToolCallContext) -> ToolCallResult: ...


class ToolProvider(Protocol):
    def open_session(self) -> AbstractAsyncContextManager[ToolSession]: ...


class McpToolProvider:
    """One MCP session per chat request: no shared connection state to repair when the server restarts."""

    def __init__(self, mcp_server_target: str | MCPServer):
        self._mcp_server_target = mcp_server_target

    @asynccontextmanager
    async def open_session(self) -> AsyncIterator["McpToolSession"]:
        async with Client(self._mcp_server_target) as client:
            yield McpToolSession(client)


class McpToolSession:
    def __init__(self, client: Client):
        self._client = client

    async def list_tools(self) -> list[ToolDefinition]:
        tools: list[ToolDefinition] = []
        cursor = None
        while True:
            listing = await self._client.list_tools(cursor=cursor)
            tools.extend(ToolDefinition(tool.name, tool.description or "", tool.input_schema) for tool in listing.tools)
            cursor = listing.next_cursor
            if cursor is None:
                return tools

    async def call_tool(self, name: str, arguments: dict[str, Any], call_context: ToolCallContext) -> ToolCallResult:
        result = await self._client.call_tool(name, arguments, meta=build_call_meta(call_context))
        return ToolCallResult(
            content=get_result_text(result),
            denied=result.is_error,
            checkpoint_steps=parse_checkpoint_steps(result),
        )


def build_call_meta(call_context: ToolCallContext) -> dict[str, str]:
    # MCP has no per-call headers; _meta carries trace context and the caller. The caller is never a tool
    # argument, because the model fills those in and could be talked into impersonating another user.
    return {
        TRACEPARENT_META_KEY: call_context.traceparent,
        USER_ID_META_KEY: call_context.caller.user_id,
        SESSION_ID_META_KEY: call_context.caller.session_id,
    }


def get_result_text(result: CallToolResult) -> str:
    text = "\n".join(block.text for block in result.content if isinstance(block, TextContent))
    if not text and result.structured_content is not None:
        return json.dumps(result.structured_content)
    return text


def parse_checkpoint_steps(result: CallToolResult) -> tuple[CheckpointStep, ...]:
    raw_steps = get_raw_checkpoint_steps(result)
    return tuple(
        CheckpointStep(str(raw_step.get("middleware", "")), str(raw_step.get("outcome", "")), str(raw_step.get("reason", "")))
        for raw_step in raw_steps
        if isinstance(raw_step, dict)
    )


def get_raw_checkpoint_steps(result: CallToolResult) -> list[Any]:
    for container in (result.structured_content, result.meta):
        if isinstance(container, dict) and isinstance(container.get(CHECKPOINT_STEPS_FIELD), list):
            return container[CHECKPOINT_STEPS_FIELD]
    return []
