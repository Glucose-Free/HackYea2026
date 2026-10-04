"""Streamable-HTTP MCP server over PolicyMiddleware, for a gateway that attests the caller on every call.

Unlike mcp_stdio_server.py (one authenticated principal per stdio process), one process serves
every user: the gateway names the user in each call's `_meta`, and the knowledge store is keyed
per user. This trusts whoever can reach the port, so the port must be reachable only by the
gateway (network isolation). See docs/contracts/data-mcp-server.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import json
from typing import Any, Callable
from uuid import uuid4

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp_types import CallToolResult, TextContent, Tool, ToolAnnotations

from policy_engine.middleware import MCPDecision, MCPRequest, PolicyMiddleware, TrustedPrincipal

SERVER_NAME = "policy-data"
USER_ID_META_KEY = "ai-control-gateway/user_id"
SESSION_ID_META_KEY = "ai-control-gateway/session_id"
CHECKPOINT_STEPS_FIELD = "checkpoint_steps"
CALLER_IDENTITY_MIDDLEWARE = "caller_identity"
PERMISSION_MIDDLEWARE = "tool_permission"
POLICY_MIDDLEWARE = "datalog_policy"
PASSED_OUTCOME = "passed"
DENIED_OUTCOME = "denied"
PERMISSION_PASSED_REASON = "tool {tool_name} is permitted for role {role}"
# Naming the rule tells the user only that it fired; the knowledge that triggered it is already theirs.
POLICY_DENIED_TEXT = "Denied by data policy ({rule_ids}): this would combine knowledge the policy keeps apart."
MISSING_CALLER_TEXT = "Denied: the request does not identify a user."
MISSING_CALLER_REASON = "no user id in _meta"
RULE_ID_SEPARATOR = ", "


@dataclass(frozen=True)
class ServedScope:
    """Tenant, role and dataset shared by every user this server answers for; set by the operator."""

    tenant_id: str
    role: str
    dataset_id: str

    def get_principal(self, user_id: str) -> TrustedPrincipal:
        return TrustedPrincipal(self.tenant_id, user_id, role=self.role, dataset_id=self.dataset_id)


@dataclass(frozen=True)
class AttestedCaller:
    user_id: str
    session_id: str


class PolicyHttpMCPServer(MCPServer):
    def __init__(self, middleware: PolicyMiddleware, execute: Callable[[str, dict], Any], scope: ServedScope):
        super().__init__(SERVER_NAME, instructions="Organization data; every call passes the disclosure policy.")
        middleware.config_store.load()  # A missing or invalid policy stops startup.
        self._middleware = middleware
        self._execute = execute
        self._scope = scope

    async def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name=descriptor["name"],
                description=descriptor["description"],
                input_schema=descriptor["inputSchema"],
                annotations=ToolAnnotations.model_validate(descriptor["annotations"]),
            )
            for descriptor in self._middleware.registry.list_tools_for_role(self._scope.role)
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Context | None = None) -> CallToolResult:
        caller = get_attested_caller(context)
        if caller is None:
            return build_missing_caller_denial()
        # A fresh request id per call: the gateway never retries a call, so the engine's replay cache is not needed.
        request = MCPRequest(uuid4().hex, caller.session_id, tool_name=name, arguments=arguments)
        handle = partial(self._middleware.handle, request,
                         principal=self._scope.get_principal(caller.user_id), execute=self._execute)
        # handle() blocks on SQLite and the executor; keep it off the event loop.
        decision = await anyio.to_thread.run_sync(handle)
        return build_tool_result(name, self._scope.role, decision)


def get_attested_caller(context: Context | None) -> AttestedCaller | None:
    meta = (context.request_context.meta if context is not None else None) or {}
    user_id = meta.get(USER_ID_META_KEY)
    if not isinstance(user_id, str) or not user_id:
        return None
    session_id = meta.get(SESSION_ID_META_KEY)
    return AttestedCaller(user_id, session_id if isinstance(session_id, str) and session_id else user_id)


def build_tool_result(tool_name: str, role: str, decision: MCPDecision) -> CallToolResult:
    if decision.allowed:
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(decision.data, ensure_ascii=False))])
    if decision.violated_rule_ids:
        return build_policy_denial(tool_name, role, decision.violated_rule_ids)
    return build_refusal(decision.reason)


def build_policy_denial(tool_name: str, role: str, violated_rule_ids: tuple[str, ...]) -> CallToolResult:
    rule_ids = RULE_ID_SEPARATOR.join(violated_rule_ids)
    return CallToolResult(
        content=[TextContent(type="text", text=POLICY_DENIED_TEXT.format(rule_ids=rule_ids))],
        is_error=True,
        structured_content={CHECKPOINT_STEPS_FIELD: [
            {"middleware": PERMISSION_MIDDLEWARE, "outcome": PASSED_OUTCOME,
             "reason": PERMISSION_PASSED_REASON.format(tool_name=tool_name, role=role)},
            {"middleware": POLICY_MIDDLEWARE, "outcome": DENIED_OUTCOME, "reason": rule_ids},
        ]},
    )


def build_missing_caller_denial() -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=MISSING_CALLER_TEXT)],
        is_error=True,
        structured_content={CHECKPOINT_STEPS_FIELD: [
            {"middleware": CALLER_IDENTITY_MIDDLEWARE, "outcome": DENIED_OUTCOME, "reason": MISSING_CALLER_REASON},
        ]},
    )


def build_refusal(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)
