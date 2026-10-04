"""Minimal MCP stdio bridge.

The server does not implement policy logic. It only converts MCP tool calls
into a request object and forwards them to the Datalog middleware.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, TextIO

from policy_middleware import DEFAULT_MIDDLEWARE, MCPRequest, PolicyRuleConfig


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]


TOOLS = [
    ToolSpec(
        name="evaluate_request",
        description="Forward the raw MCP request to the Datalog policy middleware and return its decision.",
        schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "user_id": {"type": ["string", "null"]},
                "request_text": {"type": "string"},
                "tool_name": {"type": ["string", "null"]},
                "arguments": {"type": "object", "additionalProperties": True},
                "metadata": {"type": "object", "additionalProperties": True},
                "facts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "subject": {"type": "string"},
                            "relation": {"type": "string"},
                            "value": {"type": "string"},
                            "source": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["session_id", "request_text"],
        },
    ),
    ToolSpec(
        name="snapshot_session",
        description="Inspect the stored session state for a session id.",
        schema={
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    ),
    ToolSpec(
        name="list_policy_rules",
        description="List policy rules from the JSON config file with their enabled state.",
        schema={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="set_policy_rule_enabled",
        description="Enable or disable one policy rule by rule_id.",
        schema={
            "type": "object",
            "properties": {
                "rule_id": {"type": "string"},
                "enabled": {"type": "boolean"},
            },
            "required": ["rule_id", "enabled"],
        },
    ),
    ToolSpec(
        name="upsert_policy_rule",
        description="Add or replace a policy rule in the JSON config file.",
        schema={
            "type": "object",
            "properties": {
                "rule_id": {"type": "string"},
                "enabled": {"type": "boolean"},
                "rule": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "head": {
                            "type": "object",
                            "properties": {
                                "predicate": {"type": "string"},
                                "terms": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["predicate", "terms"],
                        },
                        "body": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "predicate": {"type": "string"},
                                    "terms": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["predicate", "terms"],
                            },
                        },
                    },
                    "required": ["name", "head", "body"],
                },
            },
            "required": ["rule_id", "rule"],
        },
    ),
    ToolSpec(
        name="reload_policy_rules",
        description="Reload the JSON policy config into the live Datalog engine.",
        schema={"type": "object", "properties": {}},
    ),
]


class MCPPolicyServer:
    def __init__(self) -> None:
        self.middleware = DEFAULT_MIDDLEWARE

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {
                        "name": "mcp-datalog-boilerplate",
                        "version": "0.1.0",
                    },
                    "capabilities": {"tools": {}},
                },
            }

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {"name": tool.name, "description": tool.description, "inputSchema": tool.schema}
                        for tool in TOOLS
                    ]
                },
            }

        if method == "tools/call":
            params = message.get("params") or {}
            tool_name = params.get("name")
            arguments = params.get("arguments") or {}
            result = self.call_tool(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, ensure_ascii=False, indent=2),
                        }
                    ]
                },
            }

        if method in {"notifications/initialized", "ping"}:
            return None

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"},
        }

    def call_tool(self, tool_name: str | None, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "evaluate_request":
            request = MCPRequest(
                request_id=str(arguments.get("request_id") or arguments.get("session_id") or "req"),
                session_id=arguments["session_id"],
                user_id=arguments.get("user_id"),
                request_text=arguments["request_text"],
                tool_name=arguments.get("tool_name"),
                arguments=dict(arguments.get("arguments") or {}),
                metadata=dict(arguments.get("metadata") or {}),
                facts=list(arguments.get("facts") or []),
            )
            return self.middleware.handle(request).as_dict()

        if tool_name == "snapshot_session":
            return self.middleware.snapshot(arguments["session_id"])

        if tool_name == "list_policy_rules":
            return {"rules": self.middleware.list_rules()}

        if tool_name == "set_policy_rule_enabled":
            self.middleware.set_rule_enabled(arguments["rule_id"], bool(arguments["enabled"]))
            return {"ok": True, "rules": self.middleware.list_rules()}

        if tool_name == "upsert_policy_rule":
            rule_config = PolicyRuleConfig.from_dict(
                {
                    "rule_id": arguments["rule_id"],
                    "enabled": arguments.get("enabled", True),
                    "rule": arguments["rule"],
                }
            )
            self.middleware.config_store.upsert_rule(rule_config)
            self.middleware.reload_rules()
            return {"ok": True, "rules": self.middleware.list_rules()}

        if tool_name == "reload_policy_rules":
            self.middleware.reload_rules()
            return {"ok": True, "rules": self.middleware.list_rules()}

        return {"error": f"Unknown tool: {tool_name}"}

    def serve_stdio(self, input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout) -> None:
        while True:
            message = self._read_message(input_stream)
            if message is None:
                return
            response = self.handle(message)
            if response is not None:
                self._write_message(output_stream, response)

    @staticmethod
    def _read_message(stream: TextIO) -> dict[str, Any] | None:
        headers: dict[str, str] = {}
        while True:
            line = stream.buffer.readline()
            if not line:
                return None
            if line in {b"\r\n", b"\n"}:
                break
            key, value = line.decode("utf-8").split(":", 1)
            headers[key.strip().lower()] = value.strip()

        content_length = int(headers.get("content-length", "0"))
        if content_length <= 0:
            return None

        body = stream.buffer.read(content_length)
        if not body:
            return None
        return json.loads(body.decode("utf-8"))

    @staticmethod
    def _write_message(stream: TextIO, message: dict[str, Any]) -> None:
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        stream.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("utf-8"))
        stream.buffer.write(payload)
        stream.buffer.flush()


def main() -> None:
    MCPPolicyServer().serve_stdio()


if __name__ == "__main__":
    main()
