import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp_policy_server import (
    MAX_MESSAGE_BYTES, MCPPolicyServer, PROTOCOL_VERSION, RPCError,
)
from policy_middleware import (
    KnowledgeStore, PolicyConfigStore, PolicyMiddleware,
    PUBLIC_BLOCK_REASON, TrustedPrincipal,
)

from examples.aml import AMLToolAdapter, build_registry, demo_executor, initialize_demo

REPO_ROOT = Path(__file__).resolve().parents[2]


def demo_principal(tenant, user, role="restricted_analyst"):
    return TrustedPrincipal(tenant, user, role=role, dataset_id="bank-demo-v1")


def initialize_message(request_id=1, version=PROTOCOL_VERSION):
    return {"jsonrpc": "2.0", "id": request_id, "method": "initialize", "params": {
        "protocolVersion": version, "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "1.0"},
    }}


def initialized_message():
    return {"jsonrpc": "2.0", "method": "notifications/initialized"}


def call_message(request_id, name, arguments):
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "knowledge.sqlite")
        self.rules = str(Path(self.temp.name) / "rules.json")
        self.middleware = initialize_demo(self.db, self.rules)
        self.principal = demo_principal("bank-a", "employee-1")
        self.calls = []
        self.clock = 100.0
        self.server = self.make_server()
        self.counter = 10

    def executor(self, tool, args):
        self.calls.append((tool, dict(args)))
        return demo_executor(tool, args)

    def make_server(self, principal=None, execute=None, middleware=None):
        return MCPPolicyServer(middleware or self.middleware,
                               principal=principal or self.principal,
                               execute=execute or self.executor, clock=lambda: self.clock)

    def ready(self, server=None):
        server = server or self.server
        result = server.handle(initialize_message())
        self.assertEqual(result["result"]["protocolVersion"], PROTOCOL_VERSION)
        self.assertIsNone(server.handle(initialized_message()))
        return server

    def call(self, tool, args, server=None):
        self.counter += 1
        return (server or self.server).handle(call_message(self.counter, tool, args))

    def assert_blocked(self, response):
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"], {"allowed": False, "reason": PUBLIC_BLOCK_REASON})
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])

    def test_constructor_requires_trusted_principal_executor_and_valid_policy(self):
        for principal in (None, {"user_id": "employee-1"}, demo_principal("bank-a", "admin", "security_admin")):
            with self.assertRaises(ValueError):
                MCPPolicyServer(self.middleware, principal=principal, execute=self.executor)
        with self.assertRaises(ValueError):
            MCPPolicyServer(self.middleware, principal=self.principal, execute=None)
        Path(self.rules).unlink()
        with self.assertRaises(ValueError):
            self.make_server()

    def test_initialize_negotiates_supported_version_and_only_tools_capability(self):
        reply = self.server.handle(initialize_message(version="unsupported-client-version"))
        self.assertEqual(reply["result"]["protocolVersion"], PROTOCOL_VERSION)
        self.assertEqual(reply["result"]["capabilities"], {"tools": {"listChanged": False}})

    def test_tools_require_initialize_and_initialized_notification(self):
        self.assertEqual(self.call("get_transaction_anomalies", {"subject_id": "S17"})["error"]["code"], -32000)
        self.server.handle(initialize_message())
        self.assertEqual(self.call("get_transaction_anomalies", {"subject_id": "S17"})["error"]["code"], -32000)
        self.server.handle(initialized_message())
        self.assertFalse(self.call("get_transaction_anomalies", {"subject_id": "S17"})["result"]["isError"])
        self.assertEqual(len(self.calls), 1)

    def test_malformed_initialize_does_not_change_lifecycle(self):
        message = initialize_message()
        message["params"]["capabilities"] = None
        self.assertEqual(self.server.handle(message)["error"]["code"], -32602)
        self.assertIn("result", self.server.handle(initialize_message(2)))

    def test_invalid_initialized_notification_does_not_change_lifecycle(self):
        self.server.handle(initialize_message())
        message = initialized_message()
        message["params"] = {"user_id": "admin"}
        self.assertIsNone(self.server.handle(message))
        self.assertEqual(self.call("get_transaction_anomalies", {"subject_id": "S17"})["error"]["code"], -32000)

    def test_second_initialize_is_rejected(self):
        self.ready()
        self.assertEqual(self.server.handle(initialize_message(2))["error"]["code"], -32600)

    def test_ping_responds_with_matching_id_even_before_initialize(self):
        self.assertEqual(self.server.handle({"jsonrpc": "2.0", "id": "p", "method": "ping"}),
                         {"jsonrpc": "2.0", "id": "p", "result": {}})

    def test_notifications_never_respond_or_execute_tool(self):
        self.ready()
        messages = [
            {"jsonrpc": "2.0", "method": "ping"},
            {"jsonrpc": "2.0", "method": "unknown"},
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 12}},
            {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "get_aml_case_summary", "arguments": {"subject_id": "S17"}}},
        ]
        for message in messages:
            self.assertIsNone(self.server.handle(message))
        self.assertEqual(self.calls, [])

    def test_incoming_response_is_not_answered(self):
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "id": 3, "result": {}}))
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "id": 3, "error": {"code": -1, "message": "x"}}))

    def test_catalog_matches_adapter_and_omits_policy_admin_and_raw_evaluation(self):
        self.ready()
        listing = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        self.assertEqual({tool["name"] for tool in listing}, set(AMLToolAdapter.TOOLS))
        for tool in listing:
            field, prefix = AMLToolAdapter.TOOLS[tool["name"]]
            schema = tool["inputSchema"]
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(schema["required"], [field])
            pattern = schema["properties"][field]["pattern"]
            self.assertIsNotNone(re.search(pattern, prefix + "17"))
            self.assertIsNone(re.search(pattern, "unexpected" + prefix + "17"))
        for name in ("evaluate_request", "snapshot_session", "list_policy_rules", "set_policy_rule_enabled", "upsert_policy_rule", "reload_policy_rules"):
            self.assertEqual(self.call(name, {})["error"]["code"], -32602)
        self.assertEqual(self.calls, [])

    def test_allowed_data_is_actual_executor_result_in_both_content_formats(self):
        self.ready()
        reply = self.call("get_transaction_anomalies", {"subject_id": "S17"})["result"]
        expected = {"allowed": True, "data": {"subject_id": "S17", "anomalies": [{"pattern": "structuring", "count": 4}]}}
        self.assertFalse(reply["isError"])
        self.assertEqual(reply["structuredContent"], expected)
        self.assertEqual(json.loads(reply["content"][0]["text"]), expected)

    def test_mosaic_block_happens_before_executor_and_releases_no_identity(self):
        self.ready()
        self.call("get_aml_case_summary", {"subject_id": "S17"})
        blocked = self.call("resolve_aml_subject", {"subject_id": "S17"})
        self.assert_blocked(blocked)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("C17", json.dumps(blocked))

    def test_reverse_mosaic_order_blocks_review_status(self):
        self.ready()
        self.call("resolve_aml_subject", {"subject_id": "S17"})
        self.assert_blocked(self.call("get_aml_case_summary", {"subject_id": "S17"}))
        self.assertEqual(len(self.calls), 1)

    def test_reconnect_and_restart_keep_recipient_history(self):
        self.ready()
        self.call("get_aml_case_summary", {"subject_id": "S17"})
        restarted = PolicyMiddleware(KnowledgeStore(self.db), PolicyConfigStore(self.rules), build_registry())
        other = self.ready(self.make_server(middleware=restarted))
        self.assertNotEqual(self.server._session_id, other._session_id)
        self.assert_blocked(self.call("resolve_aml_subject", {"subject_id": "S17"}, server=other))
        self.assertEqual(len(self.calls), 1)

    def test_different_authenticated_recipient_is_isolated(self):
        self.ready()
        self.call("get_aml_case_summary", {"subject_id": "S17"})
        for principal in (demo_principal("bank-a", "employee-2"), demo_principal("bank-b", "employee-1")):
            other = self.ready(self.make_server(principal=principal))
            self.assertFalse(self.call("resolve_aml_subject", {"subject_id": "S17"}, server=other)["result"]["isError"])

    def test_client_metadata_never_changes_identity(self):
        self.ready()
        self.call("get_aml_case_summary", {"subject_id": "S17"})
        message = call_message(50, "resolve_aml_subject", {"subject_id": "S17"})
        message["params"]["_meta"] = {"user_id": "another-user", "role": "security_admin", "facts": []}
        self.assert_blocked(self.server.handle(message))
        self.assertEqual(len(self.calls), 1)

    def test_additional_identity_facts_session_sql_arguments_are_rejected(self):
        self.ready()
        for field, value in (("user_id", "admin"), ("session_id", "reset"), ("facts", []),
                             ("dataset_id", "fresh"), ("sql", "select * from customer")):
            result = self.call("get_aml_case_summary", {"subject_id": "S17", field: value})["result"]
            self.assertTrue(result["isError"])
            self.assertEqual(result["structuredContent"]["reason"], "Invalid tool arguments")
        self.assertEqual(self.calls, [])

    def test_strict_entity_ids_and_required_arguments(self):
        self.ready()
        for args in ({}, {"subject_id": None}, {"subject_id": 17}, {"subject_id": "C17"},
                     {"subject_id": "S17\n"}, {"subject_id": "S" + "1" * 13}):
            self.assertTrue(self.call("get_aml_case_summary", args)["result"]["isError"])
        self.assertEqual(self.calls, [])

    def test_wrong_tool_call_structure_is_protocol_error(self):
        self.ready()
        for params in (None, [], {"name": []}, {"name": "get_aml_case_summary", "arguments": None},
                       {"name": "get_aml_case_summary", "arguments": []}, {"name": "get_aml_case_summary", "task": {}},
                       {"name": "get_aml_case_summary", "_meta": "admin"}):
            self.counter += 1
            reply = self.server.handle({"jsonrpc": "2.0", "id": self.counter, "method": "tools/call", "params": params})
            self.assertEqual(reply["error"]["code"], -32602)
        self.assertEqual(self.calls, [])

    def test_invalid_response_and_executor_exception_have_same_generic_denial(self):
        def bad_result(tool, args):
            return {**args, "under_review": True, "private_phone": "SECRET-123"}
        def exploding(tool, args):
            raise RuntimeError("SECRET-123 /private/db/password")
        for execute in (bad_result, exploding):
            other = self.ready(self.make_server(execute=execute))
            reply = self.call("get_aml_case_summary", {"subject_id": "S17"}, server=other)
            self.assert_blocked(reply)
            self.assertNotIn("SECRET", json.dumps(reply))
        self.assertEqual(self.middleware.store.facts_for_user(self.principal), [])

    def test_invalid_policy_after_startup_blocks_before_execution(self):
        self.ready()
        Path(self.rules).write_text("{}", encoding="utf-8")
        self.assert_blocked(self.call("get_aml_case_summary", {"subject_id": "S17"}))
        self.assertEqual(self.calls, [])

    def test_unexpected_middleware_failure_is_generic_internal_error(self):
        self.ready()
        with patch.object(self.middleware, "handle", side_effect=RuntimeError("SECRET")):
            reply = self.call("get_aml_case_summary", {"subject_id": "S17"})
        self.assertEqual(reply["error"], {"code": -32603, "message": "Internal error"})
        self.assertNotIn("SECRET", json.dumps(reply))

    def test_executor_prints_do_not_escape_to_mcp_stdout(self):
        def printing(tool, args):
            print("SECRET unchecked data")
            sys.stdout.buffer.write(b"SECRET binary unchecked data\n")
            return demo_executor(tool, args)
        server = self.ready(self.make_server(execute=printing))
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            reply = self.call("get_transaction_anomalies", {"subject_id": "S17"}, server=server)
        self.assertFalse(reply["result"]["isError"])
        self.assertEqual(captured.getvalue(), "")

    def test_duplicate_rpc_id_never_reexecutes_or_overwrites_request(self):
        self.ready()
        first = call_message("query", "get_transaction_anomalies", {"subject_id": "S17"})
        self.assertIn("result", self.server.handle(first))
        self.assertEqual(self.server.handle(first)["error"]["code"], -32600)
        first["params"] = {"name": "get_aml_case_summary", "arguments": {"subject_id": "S17"}}
        self.assertEqual(self.server.handle(first)["error"]["code"], -32600)
        self.assertEqual(len(self.calls), 1)

    def test_integer_and_string_rpc_ids_remain_distinct(self):
        self.assertEqual(self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})["id"], 2)
        self.assertEqual(self.server.handle({"jsonrpc": "2.0", "id": "2", "method": "ping"})["id"], "2")

    def test_invalid_jsonrpc_batches_and_ids_are_rejected(self):
        for message in (None, [], [initialize_message()], {"id": 1, "method": "ping"},
                        {"jsonrpc": "2.0", "id": None, "method": "ping"},
                        {"jsonrpc": "2.0", "id": True, "method": "ping"},
                        {"jsonrpc": "2.0", "id": 1.5, "method": "ping"},
                        {"jsonrpc": "2.0", "id": 2, "method": []}):
            self.assertEqual(self.server.handle(message)["error"]["code"], -32600)

    def test_unknown_method_and_cursor_are_protocol_errors(self):
        self.ready()
        self.assertEqual(self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "admin/reset"})["error"]["code"], -32601)
        self.assertEqual(self.server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"cursor": "x"}})["error"]["code"], -32602)

    def test_tool_rate_limit_refills_without_executing_denied_calls(self):
        self.ready()
        for _ in range(20):
            self.assertFalse(self.call("get_transaction_anomalies", {"subject_id": "S17"})["result"]["isError"])
        limited = self.call("get_transaction_anomalies", {"subject_id": "S17"})["result"]
        self.assertTrue(limited["isError"])
        self.assertEqual(len(self.calls), 20)
        self.clock += 1
        self.assertFalse(self.call("get_transaction_anomalies", {"subject_id": "S17"})["result"]["isError"])
        self.assertEqual(len(self.calls), 21)

    def test_session_request_limit_closes_without_data_query(self):
        self.ready()
        with patch("mcp_policy_server.MAX_REQUESTS_PER_SESSION", 1):
            self.assertEqual(self.call("get_transaction_anomalies", {"subject_id": "S17"})["error"]["code"], -32000)
        self.assertTrue(self.server._closing)
        self.assertEqual(self.calls, [])

    def test_newline_stdio_and_ping_notification_produce_only_request_responses(self):
        messages = [initialize_message(), initialized_message(),
                    {"jsonrpc": "2.0", "method": "ping"},
                    {"jsonrpc": "2.0", "id": 2, "method": "ping"}]
        source = io.BytesIO(b"".join((json.dumps(m) + "\n").encode() for m in messages))
        target = io.BytesIO()
        self.server.serve_stdio(source, target)
        lines = target.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertNotIn(b"Content-Length", target.getvalue())
        self.assertEqual(json.loads(lines[-1]), {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_invalid_json_duplicate_keys_utf8_and_nonfinite_frames_recover(self):
        malformed = [b"{oops}\n", b' {"id":1,"id":2}\n', b' {"x": NaN}\n', b' {"x": Infinity}\n', b"\xff\n"]
        ping = b'{"jsonrpc":"2.0","id":2,"method":"ping"}\n'
        target = io.BytesIO()
        self.server.serve_stdio(io.BytesIO(b"".join(malformed) + ping), target)
        responses = [json.loads(line) for line in target.getvalue().splitlines()]
        self.assertEqual([r["error"]["code"] for r in responses[:-1]], [-32700] * len(malformed))
        self.assertEqual(responses[-1]["result"], {})

    def test_oversized_frame_is_drained_and_next_message_processed(self):
        ping = b'{"jsonrpc":"2.0","id":2,"method":"ping"}\n'
        target = io.BytesIO()
        self.server.serve_stdio(io.BytesIO(b"x" * (MAX_MESSAGE_BYTES * 2) + b"\n" + ping), target)
        replies = [json.loads(line) for line in target.getvalue().splitlines()]
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0]["error"]["code"], -32600)
        self.assertEqual(replies[1]["result"], {})

    def test_text_stream_oversized_utf8_and_truncated_frame(self):
        for text in ('"' + "ż" * (MAX_MESSAGE_BYTES // 2) + '"\n', '{"jsonrpc":"2.0"}'):
            with self.assertRaises(RPCError):
                MCPPolicyServer._read_message(io.StringIO(text))
        target = io.StringIO()
        MCPPolicyServer._write_message(target, {"jsonrpc": "2.0", "id": 1, "result": {"text": "ż\nółć"}})
        self.assertEqual(len(target.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(target.getvalue())["result"]["text"], "ż\nółć")

    def test_direct_handle_bounds_input_and_rejects_nonfinite_values(self):
        self.assertEqual(self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {"x": "a" * MAX_MESSAGE_BYTES}})["error"]["code"], -32600)
        self.assertEqual(self.server.handle({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {"x": float("inf")}})["error"]["code"], -32602)

    def test_real_subprocess_mcp_handshake_and_mosaic_persists_across_processes(self):
        root = REPO_ROOT
        command = [sys.executable, str(root / "mcp_policy_server.py"), "--backend", "examples.aml:create_demo_backend", "--db", self.db, "--rules", self.rules]
        messages = [initialize_message(), initialized_message(),
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    call_message(3, "get_aml_case_summary", {"subject_id": "S17"}),
                    call_message(4, "resolve_aml_subject", {"subject_id": "S17"})]
        completed = subprocess.run(command, input="".join(json.dumps(m) + "\n" for m in messages),
                                   text=True, capture_output=True, cwd=root, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        replies = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4])
        self.assertFalse(replies[2]["result"]["isError"])
        self.assert_blocked(replies[3])
        restarted = subprocess.run(command, input="".join(json.dumps(m) + "\n" for m in
            [initialize_message(), initialized_message(), call_message(2, "resolve_aml_subject", {"subject_id": "S17"})]),
            text=True, capture_output=True, cwd=root, timeout=10)
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        self.assert_blocked(json.loads(restarted.stdout.splitlines()[-1]))

    def test_production_backend_factory_is_recipient_bound_and_stdout_is_suppressed(self):
        root = REPO_ROOT
        backend = Path(self.temp.name) / "test_backend.py"
        backend.write_text('''from policy_middleware import TrustedPrincipal
from examples.aml import demo_executor, build_registry
def create_backend():
    print("SECRET factory output")
    return TrustedPrincipal("verified-tenant", "verified-user", role="restricted_analyst", dataset_id="bank-demo-v1"), build_registry(), demo_executor
''', encoding="utf-8")
        import os
        env = {**os.environ, "PYTHONPATH": self.temp.name}
        command = [sys.executable, str(root / "mcp_policy_server.py"), "--backend", "test_backend:create_backend",
                   "--db", self.db, "--rules", self.rules]
        messages = [initialize_message(), initialized_message(), call_message(2, "get_aml_case_summary", {"subject_id": "S17"})]
        completed = subprocess.run(command, input="".join(json.dumps(m) + "\n" for m in messages),
                                   text=True, capture_output=True, cwd=root, env=env, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("SECRET", completed.stdout + completed.stderr)
        self.assertFalse(json.loads(completed.stdout.splitlines()[-1])["result"]["isError"])
        verified = demo_principal("verified-tenant", "verified-user")
        self.assertTrue(self.middleware.store.facts_for_user(verified))
        self.assertEqual(self.middleware.store.facts_for_user(self.principal), [])

    def test_production_mode_does_not_bootstrap_missing_policy(self):
        root = REPO_ROOT
        backend = Path(self.temp.name) / "test_backend.py"
        backend.write_text('''from policy_middleware import TrustedPrincipal
from examples.aml import demo_executor, build_registry
def create_backend():
    return TrustedPrincipal("verified-tenant", "verified-user", role="restricted_analyst", dataset_id="bank-demo-v1"), build_registry(), demo_executor
''', encoding="utf-8")
        import os
        missing = str(Path(self.temp.name) / "missing.json")
        completed = subprocess.run([sys.executable, str(root / "mcp_policy_server.py"), "--backend", "test_backend:create_backend",
            "--db", self.db, "--rules", missing], input="", text=True, capture_output=True, cwd=root,
            env={**os.environ, "PYTHONPATH": self.temp.name}, timeout=10)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertFalse(Path(missing).exists())


if __name__ == "__main__":
    unittest.main()
