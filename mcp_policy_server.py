"""Tools-only MCP stdio bridge; all disclosures pass through domain-independent policy middleware.

One process/connection serves one authenticated recipient. The trusted host supplies
the principal and a recipient-bound, read-only executor. MCP input never supplies
authentication, policy rules, history, or facts. Python 3.11+, no third-party packages.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import importlib
import json
import os
import re
import sys
import time
from typing import Any, BinaryIO, Callable, TextIO
from uuid import uuid4

from policy_middleware import (
    KnowledgeStore, MCPRequest, PolicyConfigStore, PolicyError,
    PolicyMiddleware, PUBLIC_BLOCK_REASON, ToolRegistry, TrustedPrincipal,
)

PROTOCOL_VERSION = "2025-11-25"
MAX_MESSAGE_BYTES = 65536
MAX_REQUESTS_PER_SESSION = 10000


class RPCError(ValueError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def _fields(value: Any, required: set[str], optional: set[str] = frozenset()) -> dict:
    if (not isinstance(value, dict) or not required <= value.keys()
            or value.keys() - required - optional):
        raise RPCError(-32602, "Invalid parameters")
    if "_meta" in value and not isinstance(value["_meta"], dict):
        raise RPCError(-32602, "Invalid parameters")
    return value


def _reject_constant(value: str):
    raise ValueError("Non-finite JSON number")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class MCPPolicyServer:
    def __init__(self, middleware: PolicyMiddleware, *, principal: TrustedPrincipal,
                 execute: Callable[[str, dict], Any], clock: Callable[[], float] = time.monotonic):
        if not isinstance(principal, TrustedPrincipal):
            raise ValueError("An authenticated, authorized recipient is required")
        if not isinstance(middleware, PolicyMiddleware) or not callable(execute):
            raise ValueError("A middleware and trusted read-only executor are required")
        if not middleware.registry.names_for(principal):
            raise ValueError("No tools authorized for this recipient")
        middleware.config_store.load()  # Missing/invalid policy stops startup.
        self.middleware = middleware
        self._principal = principal
        self._execute = execute
        self._session_id = uuid4().hex
        self._state = "new"
        self._seen_ids: set[tuple[type, Any]] = set()
        self._closing = False
        self._clock = clock
        self._tokens = 20.0  # Burst of 20, then one tool invocation per second.
        self._last_refill = clock()

    def handle(self, message: Any) -> dict | None:
        """Validate JSON-RPC, negotiate lifecycle, dispatch only the closed tool catalog."""
        request_id = None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, -32600, "Invalid request")
        if "method" not in message and ("result" in message or "error" in message):
            return None  # Never answer a response; this profile sends no server requests.
        if "id" in message:
            request_id = message["id"]
            if not (type(request_id) is int or
                    (isinstance(request_id, str) and len(request_id) <= 128)):
                return _error(None, -32600, "Invalid request")
        if not isinstance(message.get("method"), str):
            return _error(request_id, -32600, "Invalid request")
        notification = "id" not in message
        if notification:
            # A tools/call without an ID MUST NOT execute a data query.
            if message["method"] == "notifications/initialized" and self._state == "initializing":
                try:
                    _fields(message, {"jsonrpc", "method"}, {"params"})
                    _fields(message.get("params", {}), set(), {"_meta"})
                    self._state = "ready"
                except RPCError:
                    pass
            return None
        key = (type(request_id), request_id)
        if key in self._seen_ids:
            return _error(request_id, -32600, "Request ID already used")
        if self._closing or len(self._seen_ids) >= MAX_REQUESTS_PER_SESSION:
            self._closing = True
            return _error(request_id, -32000, "Session limit reached; reconnect")
        self._seen_ids.add(key)
        try:
            # Also bound/validate direct Python calls, not just wire frames. Detach arguments.
            encoded = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > MAX_MESSAGE_BYTES:
                raise RPCError(-32600, "Message too large")
            message = json.loads(encoded)
            _fields(message, {"jsonrpc", "id", "method"}, {"params"})
            params = message.get("params", {})
            if not isinstance(params, dict):
                raise RPCError(-32602, "Invalid parameters")
            result = self._dispatch(message["method"], params)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except RPCError as error:
            return _error(request_id, error.code, str(error))
        except (TypeError, ValueError, RecursionError):
            return _error(request_id, -32602, "Invalid parameters")
        except Exception:
            # No backend exceptions, paths, rule names or private responses on stdout/stderr.
            return _error(request_id, -32603, "Internal error")

    def _dispatch(self, method: str, params: dict) -> dict:
        if method == "ping":
            _fields(params, set(), {"_meta"})
            return {}
        if method == "initialize":
            if self._state != "new":
                raise RPCError(-32600, "Already initialized")
            _fields(params, {"protocolVersion", "capabilities", "clientInfo"}, {"_meta"})
            info = params["clientInfo"]
            if (not isinstance(params["protocolVersion"], str)
                    or not isinstance(params["capabilities"], dict) or not isinstance(info, dict)
                    or not isinstance(info.get("name"), str) or not isinstance(info.get("version"), str)):
                raise RPCError(-32602, "Invalid parameters")
            self._state = "initializing"
            # Return our supported version; clients supporting it continue, others disconnect.
            return {"protocolVersion": PROTOCOL_VERSION,
                    "serverInfo": {"name": "disclosure-policy-mcp", "version": "0.2.0"},
                    "capabilities": {"tools": {"listChanged": False}}}
        if method not in {"tools/list", "tools/call"}:
            raise RPCError(-32601, "Method not found")
        if self._state != "ready":
            raise RPCError(-32000, "Initialization required")
        if method == "tools/list":
            _fields(params, set(), {"_meta", "cursor"})
            if "cursor" in params:
                raise RPCError(-32602, "Invalid cursor")  # Entire catalog fits in one page.
            return {"tools": self.middleware.registry.list_tools(self._principal)}
        _fields(params, {"name"}, {"arguments", "_meta"})
        if not isinstance(params["name"], str) or not isinstance(params.get("arguments", {}), dict):
            raise RPCError(-32602, "Invalid parameters")
        if params["name"] not in self.middleware.registry.names_for(self._principal):
            raise RPCError(-32602, "Unknown tool")
        return self.call_tool(params["name"], params.get("arguments", {}))

    @staticmethod
    def _tool_result(payload: dict, *, is_error: bool) -> dict:
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=True,
                                                              allow_nan=False)}],
                "structuredContent": payload, "isError": is_error}

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Trusted dispatch only; business/input failures are MCP tool errors."""
        if self._state != "ready" or self._closing:
            raise RPCError(-32000, "Initialization required")
        if tool_name not in self.middleware.registry.names_for(self._principal):
            raise RPCError(-32602, "Unknown tool")
        now = self._clock()
        self._tokens = min(20.0, self._tokens + max(0.0, now - self._last_refill))
        self._last_refill = now
        if self._tokens < 1.0:
            return self._tool_result({"allowed": False, "reason": "Tool rate limit exceeded"}, is_error=True)
        self._tokens -= 1.0
        try:
            args = self.middleware.registry.arguments(tool_name, arguments)
        except PolicyError:
            return self._tool_result({"allowed": False, "reason": "Invalid tool arguments"}, is_error=True)
        request = MCPRequest(request_id=uuid4().hex, session_id=self._session_id,
                             tool_name=tool_name, arguments=args)
        # Accidental prints in an executor must never corrupt MCP or release unchecked data.
        # This synchronous stdio profile owns its process; do not use redirect_stdout in a shared host.
        with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink):
            decision = self.middleware.handle(request, principal=self._principal, execute=self._execute)
        if not decision.allowed:
            return self._tool_result({"allowed": False, "reason": PUBLIC_BLOCK_REASON}, is_error=True)
        return self._tool_result({"allowed": True, "data": decision.data}, is_error=False)

    def serve_stdio(self, input_stream: BinaryIO | TextIO | None = None,
                    output_stream: BinaryIO | TextIO | None = None) -> None:
        source = input_stream if input_stream is not None else sys.stdin.buffer
        target = output_stream if output_stream is not None else sys.stdout.buffer
        while not self._closing:
            try:
                message = self._read_message(source)
            except EOFError:
                return
            except RPCError as error:
                self._write_message(target, _error(None, error.code, str(error)))
                continue
            response = self.handle(message)
            if response is not None:
                self._write_message(target, response)

    @staticmethod
    def _read_message(stream: BinaryIO | TextIO) -> Any:
        line = stream.readline(MAX_MESSAGE_BYTES + 1)
        if not line:
            raise EOFError
        newline = b"\n" if isinstance(line, bytes) else "\n"
        if len(line) > MAX_MESSAGE_BYTES:
            # Drain in bounded pieces, so the next message remains aligned.
            while line and not line.endswith(newline):
                line = stream.readline(MAX_MESSAGE_BYTES + 1)
            raise RPCError(-32600, "Message too large")
        try:
            if not line.endswith(newline):
                raise ValueError("Missing frame delimiter")
            if isinstance(line, bytes):
                line = line.decode("utf-8")
            if len(line.encode("utf-8")) > MAX_MESSAGE_BYTES:
                raise RPCError(-32600, "Message too large")
            return json.loads(line, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        except RPCError:
            raise
        except (UnicodeError, ValueError, RecursionError):
            raise RPCError(-32700, "Parse error") from None

    @staticmethod
    def _write_message(stream: BinaryIO | TextIO, message: dict) -> None:
        data = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"
        target = getattr(stream, "buffer", stream)
        try:
            target.write(data.encode("utf-8"))
        except TypeError:
            target.write(data)
        target.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True,
                        help="Trusted module:factory returning (verified principal, tool registry, bound executor)")
    parser.add_argument("--db", required=True, help="Persistent disclosure history; never reset per connection")
    parser.add_argument("--rules", required=True, help="Administrator-managed policy JSON")
    options = parser.parse_args(argv)
    try:
        # Import/factory output is also excluded from the protocol stream.
        with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink):
            if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", options.backend):
                raise ValueError("Invalid backend factory")
            module, factory = options.backend.split(":")
            principal, registry, execute = getattr(importlib.import_module(module), factory)()
            if not isinstance(registry, ToolRegistry):
                raise ValueError("A trusted tool registry is required")
            config = PolicyConfigStore(options.rules)
            config.load()  # Never bootstrap a policy from the generic server.
            middleware = PolicyMiddleware(KnowledgeStore(options.db), config, registry)
            server = MCPPolicyServer(middleware, principal=principal, execute=execute)
        server.serve_stdio()
        return 0
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
    except Exception:
        print("MCP startup or transport failed; check trusted host configuration.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
