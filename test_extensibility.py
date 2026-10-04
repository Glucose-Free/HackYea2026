from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from examples import aml, transactions
from mcp_policy_server import MCPPolicyServer
from policy_middleware import (
    DatalogAtom, DatalogProgram, DatalogRule, KnowledgeFact, KnowledgeStore, MCPRequest,
    PlannedFact, PolicyConfigStore, PolicyError, PolicyMiddleware, PolicyRuleConfig,
    ToolRegistry, TrustedPrincipal, ValueDomain,
)


def initialize(server):
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "generic-client", "version": "1"}}})
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return server


class ExtensibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "history.sqlite")
        self.rules = str(Path(self.temp.name) / "rules.json")
        self.middleware = transactions.initialize_demo(self.db, self.rules)
        self.principal = TrustedPrincipal("organization", "user", role="transaction_reader", dataset_id="stable-data")
        self.calls = []
        self.counter = 0

    def executor(self, name, args):
        self.calls.append((name, args))
        return transactions.demo_executor(name, args)

    def call(self, name, transaction="wire-transfer-17", *, middleware=None, principal=None, execute=None, request_id=None):
        self.counter += 1
        return (middleware or self.middleware).handle(
            MCPRequest(request_id or f"request-{self.counter}", "session", tool_name=name,
                       arguments={"transaction_id": transaction}),
            principal=principal or self.principal, execute=execute or self.executor)

    def with_tools(self, tools):
        return PolicyMiddleware(KnowledgeStore(self.db), PolicyConfigStore(self.rules), ToolRegistry(tools))

    def test_second_domain_blocks_both_parties_before_execution(self):
        self.assertTrue(self.call("get_transaction_sender").allowed)
        result = self.call("get_transaction_recipient")
        self.assertFalse(result.allowed)
        self.assertIsNone(result.data)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.middleware.store.facts_for_user(self.principal), [
            KnowledgeFact("wire-transfer-17", "transaction.sender", "company-acme")])

    def test_reverse_order_also_blocks(self):
        self.assertTrue(self.call("get_transaction_recipient").allowed)
        self.assertFalse(self.call("get_transaction_sender").allowed)
        self.assertEqual(len(self.calls), 1)

    def test_parties_from_different_transactions_are_allowed(self):
        self.assertTrue(self.call("get_transaction_sender", "wire-transfer-17").allowed)
        self.assertTrue(self.call("get_transaction_recipient", "wire-transfer-18").allowed)

    def test_mcp_catalog_and_calls_work_with_no_bank_id_format_or_aml_role(self):
        server = initialize(MCPPolicyServer(self.middleware, principal=self.principal, execute=self.executor))
        listing = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual({tool["name"] for tool in listing["result"]["tools"]}, set(transactions.RELATIONS))
        for request_id, name, error in ((3, "get_transaction_sender", False), (4, "get_transaction_recipient", True)):
            reply = server.handle({"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {
                "name": name, "arguments": {"transaction_id": "wire-transfer-17"}}})
            self.assertEqual(reply["result"]["isError"], error)
        self.assertEqual(len(self.calls), 1)

    def test_acl_filters_catalog_and_direct_middleware_calls(self):
        tools = transactions.tool_definitions()
        tools = [replace(tools[0], allowed_roles=frozenset({"sender_reader"})),
                 replace(tools[1], allowed_roles=frozenset({"recipient_reader"}))]
        middleware = self.with_tools(tools)
        principal = replace(self.principal, role="sender_reader")
        server = initialize(MCPPolicyServer(middleware, principal=principal, execute=self.executor))
        listing = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual([tool["name"] for tool in listing["result"]["tools"]], ["get_transaction_sender"])
        reply = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "get_transaction_recipient", "arguments": {"transaction_id": "wire-transfer-17"}}})
        self.assertIn("error", reply)
        self.assertFalse(self.call("get_transaction_recipient", middleware=middleware, principal=principal).allowed)
        self.assertEqual(self.calls, [])

    def test_cache_replay_cannot_bypass_changed_tool_acl(self):
        self.assertTrue(self.call("get_transaction_sender", request_id="same").allowed)
        tools = [replace(tool, allowed_roles=frozenset({"another_role"})) for tool in transactions.tool_definitions()]
        narrowed = self.with_tools(tools)
        self.assertFalse(self.call("get_transaction_sender", middleware=narrowed, request_id="same").allowed)
        self.assertEqual(len(self.calls), 1)

    def test_combined_registry_supports_two_domains_without_core_changes(self):
        tools = [replace(tool, allowed_roles=frozenset({"demo_operator"}))
                 for tool in (*aml.tool_definitions(), *transactions.tool_definitions())]
        config = PolicyConfigStore(self.rules)
        config.save(aml.default_rules() + transactions.default_rules(), "combined-v1")
        middleware = self.with_tools(tools)
        principal = replace(self.principal, role="demo_operator")
        def execute(name, args):
            return transactions.demo_executor(name, args) if name in transactions.RELATIONS else aml.demo_executor(name, args)
        self.assertTrue(self.call("get_transaction_sender", middleware=middleware, principal=principal, execute=execute).allowed)
        aml_request = MCPRequest("aml-status", "session", tool_name="get_aml_case_summary", arguments={"subject_id": "S17"})
        self.assertTrue(middleware.handle(aml_request, principal=principal, execute=execute).allowed)
        self.assertFalse(self.call("get_transaction_recipient", middleware=middleware, principal=principal, execute=execute).allowed)
        identity = MCPRequest("aml-identity", "session", tool_name="resolve_aml_subject", arguments={"subject_id": "S17"})
        self.assertFalse(middleware.handle(identity, principal=principal, execute=execute).allowed)
        self.assertEqual(len(middleware.registry.list_tools(principal)), 9)

    def test_new_policy_rule_takes_effect_without_new_tool_or_adapter_code(self):
        rule = PolicyRuleConfig("restrict_literal_company", DatalogRule("restrict_literal_company",
            DatalogAtom("violation", ("?u", "?s", "literal_company")),
            (DatalogAtom("knows", ("?u", "?s", "transaction.sender", "company-acme")),)))
        self.middleware.config_store.save(transactions.default_rules() + [rule], "transactions-v2")
        self.assertFalse(self.call("get_transaction_sender").allowed)
        # Preflight checks every possible company, rather than reading whether the secret matches.
        self.assertEqual(self.calls, [])
        self.assertTrue(self.call("get_transaction_recipient").allowed)

    def test_unknown_output_subject_is_planned_and_literal_rule_blocks_before_execution(self):
        base = transactions.tool_definitions()[0]
        tool = replace(base,
            plan_disclosure=lambda args, history: [PlannedFact(ValueDomain(pattern=r"person-[0-9]+"), "profile.location", "disclosed")],
            validate_response=lambda args, data: (data, [KnowledgeFact("person-9", "profile.location", "disclosed")]))
        rule = PolicyRuleConfig("private_person", DatalogRule("private_person",
            DatalogAtom("violation", ("?u", "person-9", "location")),
            (DatalogAtom("knows", ("?u", "person-9", "profile.location", "disclosed")),)))
        self.middleware.config_store.save([rule], "profiles-v1")
        self.assertFalse(self.call(base.name, middleware=self.with_tools([tool])).allowed)
        self.assertEqual(self.calls, [])

    def test_finite_value_domain_works_with_arbitrary_status_relation(self):
        base = transactions.tool_definitions()[0]
        tool = replace(base,
            plan_disclosure=lambda args, history: [PlannedFact(args["transaction_id"], "document.classification", ValueDomain(values=frozenset({"public", "private"})))],
            validate_response=lambda args, data: (data, [KnowledgeFact(args["transaction_id"], "document.classification", "public")]))
        rule = PolicyRuleConfig("private_classification", DatalogRule("private_classification",
            DatalogAtom("violation", ("?u", "?s", "private_classification")),
            (DatalogAtom("knows", ("?u", "?s", "document.classification", "private")),)))
        self.middleware.config_store.save([rule], "documents-v1")
        self.assertFalse(self.call(base.name, middleware=self.with_tools([tool])).allowed)
        self.assertEqual(self.calls, [])

    def test_unknowns_can_equal_each_other_in_a_new_rule(self):
        base = transactions.tool_definitions()[0]
        tool = replace(base, plan_disclosure=lambda args, history: [
            PlannedFact(args["transaction_id"], "edge.left", ValueDomain()),
            PlannedFact(args["transaction_id"], "edge.right", ValueDomain())])
        rule = PolicyRuleConfig("equal_edges", DatalogRule("equal_edges",
            DatalogAtom("violation", ("?u", "?s", "equal_edges")),
            (DatalogAtom("knows", ("?u", "?s", "edge.left", "?v")),
             DatalogAtom("knows", ("?u", "?s", "edge.right", "?v")))))
        self.middleware.config_store.save([rule], "edges-v1")
        self.assertFalse(self.call(base.name, middleware=self.with_tools([tool])).allowed)
        self.assertEqual(self.calls, [])

    def test_undeclared_response_facts_fail_closed_and_are_not_recorded(self):
        base = transactions.tool_definitions()[0]
        tool = replace(base, plan_disclosure=lambda args, history: [])
        self.assertFalse(self.call(base.name, middleware=self.with_tools([tool])).allowed)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.middleware.store.facts_for_user(self.principal), [])

    def test_duplicate_tool_names_and_missing_contracts_are_rejected(self):
        tool = transactions.tool_definitions()[0]
        with self.assertRaises(PolicyError):
            ToolRegistry([tool, tool])
        with self.assertRaises(PolicyError):
            ToolRegistry([])
        for update in ({"allowed_roles": frozenset()}, {"plan_disclosure": None},
                       {"validate_response": None}, {"input_schema": {"type": "object"}}):
            with self.assertRaises(PolicyError):
                replace(tool, **update)

    def test_registry_schema_snapshot_is_not_changed_by_external_mutation(self):
        tool = transactions.tool_definitions()[0]
        registry = ToolRegistry([tool])
        tool.input_schema["properties"]["transaction_id"]["type"] = "number"
        first = registry.list_tools(self.principal)
        self.assertEqual(first[0]["inputSchema"]["properties"]["transaction_id"]["type"], "string")
        first[0]["inputSchema"]["properties"]["transaction_id"]["type"] = "number"
        self.assertEqual(registry.list_tools(self.principal)[0]["inputSchema"]["properties"]["transaction_id"]["type"], "string")

    def test_registry_limits_reject_oversized_plans_and_responses(self):
        base = transactions.tool_definitions()[0]
        tool = replace(base, plan_disclosure=lambda args, history: (PlannedFact("s", "r", "v") for _ in range(100)))
        registry = ToolRegistry([tool], max_disclosure_facts=2, max_result_bytes=100)
        with self.assertRaises(PolicyError):
            registry.plan(tool.name, {"transaction_id": "t"}, [])
        with self.assertRaises(PolicyError):
            registry.disclosure(tool.name, {"transaction_id": "t"}, {"transaction_id": "t", "company_id": "x" * 150})
        with self.assertRaises(PolicyError):
            registry.disclosure(tool.name, {"transaction_id": "t"}, float("nan"))

    def test_many_independent_rules_use_indexes_with_a_bounded_match_budget(self):
        rules = []
        facts = []
        for i in range(150):
            name = f"rule-{i}"
            rules.append(PolicyRuleConfig(name, DatalogRule(name,
                DatalogAtom("violation", ("?u", "?s", name)),
                (DatalogAtom("knows", ("?u", "?s", f"relation-{i}", "?v")),))))
            facts.append(DatalogAtom("knows", ("recipient", f"entity-{i}", f"relation-{i}", "value")))
        self.middleware.config_store.save(rules, "many-rules-v1")
        _, loaded = self.middleware.config_store.load()
        self.assertEqual(len(loaded), 150)
        program = DatalogProgram([r.rule for r in loaded], max_matches=500)
        self.assertEqual(len([f for f in program.infer(facts) if f.predicate == "violation"]), 150)

    def test_generic_domain_expansion_is_bounded_before_execution(self):
        base = transactions.tool_definitions()[0]
        domain = ValueDomain(values=frozenset({"v1", "v2", "v3", "v4", "v5"}))
        tool = replace(base, plan_disclosure=lambda args, history: [PlannedFact(domain, domain, domain)])
        with patch("policy_middleware.DatalogProgram", lambda rules: DatalogProgram(rules, max_matches=10)):
            self.assertFalse(self.call(base.name, middleware=self.with_tools([tool])).allowed)
        self.assertEqual(self.calls, [])

    def test_configurable_rule_limit_fails_closed(self):
        store = PolicyConfigStore(self.rules, max_rules=1)
        with self.assertRaises(PolicyError):
            store.save(transactions.default_rules() * 2, "too-many")
        self.assertEqual(self.middleware.config_store.load()[0], "transactions-v1")

    def test_generic_core_and_mcp_import_without_any_example_modules(self):
        root = Path(__file__).resolve().parent
        isolated = Path(self.temp.name) / "isolated"
        isolated.mkdir()
        for filename in ("policy_middleware.py", "mcp_policy_server.py"):
            (isolated / filename).write_bytes((root / filename).read_bytes())
        program = '''import sys
import policy_middleware
import mcp_policy_server
assert not any(name == "examples" or name.startswith("examples.") for name in sys.modules)
'''
        result = subprocess.run([sys.executable, "-c", program], cwd=isolated, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_second_domain_actual_stdio_subprocess(self):
        root = Path(__file__).resolve().parent
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "get_transaction_sender", "arguments": {"transaction_id": "wire-transfer-17"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "get_transaction_recipient", "arguments": {"transaction_id": "wire-transfer-17"}}},
        ]
        result = subprocess.run([sys.executable, str(root / "mcp_policy_server.py"),
            "--backend", "examples.transactions:create_demo_backend", "--db", self.db, "--rules", self.rules],
            input="".join(json.dumps(m) + "\n" for m in messages), cwd=root, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertFalse(replies[1]["result"]["isError"])
        self.assertTrue(replies[2]["result"]["isError"])
        self.assertNotIn("company-beta", json.dumps(replies[2]))


if __name__ == "__main__":
    unittest.main()
