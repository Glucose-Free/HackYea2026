import concurrent.futures
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from policy_engine.middleware import (
    DatalogAtom, DatalogProgram, DatalogRule, InferenceLimit, KnowledgeFact, KnowledgeStore,
    LegacyDatabaseError, MCPRequest, PolicyConfigStore, PolicyError, PolicyMiddleware,
    PolicyRuleConfig, TrustedPrincipal,
)

from examples.aml import AMLToolAdapter, build_registry, default_rules as aml_default_rules, demo_executor, initialize_demo


def demo_principal(tenant, user, role="restricted_analyst"):
    return TrustedPrincipal(tenant, user, role=role, dataset_id="bank-demo-v1")


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "knowledge.sqlite")
        self.rules = str(Path(self.temp.name) / "rules.json")
        self.middleware = initialize_demo(self.db, self.rules)
        self.principal = demo_principal("bank-a", "employee-1")
        self.calls = []
        self.review = True
        self.counter = 0

    def executor(self, tool, args):
        self.calls.append((tool, args))
        if tool == "get_aml_case_summary":
            return {**args, "under_review": self.review}
        if tool == "resolve_aml_subject":
            return {**args, "customer_id": "C17"}
        if tool == "get_account_owner":
            return {**args, "customer_id": "C17"}
        if tool == "get_blocked_accounts":
            return {**args, "accounts": [{"account_id": "A9", "blocked": True}]}
        if tool == "get_customer_contact":
            return {**args, "phone": "+000000000", "address": "Fictional address"}
        if tool == "get_customer_workplace":
            return {**args, "employer": "Fictional employer"}
        if tool == "get_transaction_anomalies":
            return {**args, "anomalies": [{"pattern": "structuring", "count": 4}]}
        raise AssertionError("Unknown tool executed")

    def call(self, tool, args, *, principal=None, session="session-1", request_id=None, execute=None):
        self.counter += 1
        return self.middleware.handle(MCPRequest(request_id or f"r{self.counter}", session,
            tool_name=tool, arguments=args), principal=principal or self.principal,
            execute=execute or self.executor)

    def test_approved_result_has_entity_and_real_status(self):
        self.review = False
        reply = self.call("get_aml_case_summary", {"subject_id": "S17"})
        self.assertTrue(reply.allowed)
        self.assertEqual(reply.data["under_review"], False)
        self.assertIn(KnowledgeFact("S17", "aml_review", "false"),
                      self.middleware.store.facts_for_user(self.principal))

    def test_different_clients_do_not_collide(self):
        self.assertTrue(self.call("get_aml_case_summary", {"subject_id": "S17"}).allowed)
        self.assertTrue(self.call("get_customer_contact", {"customer_id": "C99"}).allowed)

    def test_direct_identity_is_blocked_before_execution(self):
        self.call("get_aml_case_summary", {"subject_id": "S17"})
        before = len(self.calls)
        reply = self.call("resolve_aml_subject", {"subject_id": "S17"})
        self.assertFalse(reply.allowed)
        self.assertIsNone(reply.data)
        self.assertEqual(len(self.calls), before)

    def test_policy_violation_names_the_violated_rules_for_audit_only(self):
        self.call("get_aml_case_summary", {"subject_id": "S17"})
        reply = self.call("resolve_aml_subject", {"subject_id": "S17"})
        self.assertEqual(reply.violated_rule_ids, ("block_aml_identity",))
        self.assertNotIn("block_aml_identity", json.dumps(reply.as_dict()))

    def test_refusal_that_is_not_a_policy_violation_names_no_rules(self):
        reply = self.call("resolve_aml_subject", {"subject_id": "not-an-id"})
        self.assertFalse(reply.allowed)
        self.assertEqual(reply.violated_rule_ids, ())

    def test_reverse_order_is_blocked(self):
        self.call("resolve_aml_subject", {"subject_id": "S17"})
        self.assertFalse(self.call("get_aml_case_summary", {"subject_id": "S17"}).allowed)
        self.assertEqual(len(self.calls), 1)

    def disable_rule(self, rule_id):
        rules = [PolicyRuleConfig(r.rule_id, r.rule, enabled=r.rule_id != rule_id)
                 for r in aml_default_rules()]
        self.middleware.config_store.save(rules, "aml-without-" + rule_id)

    def test_contact_of_identified_aml_subject_is_blocked_even_when_identity_is_allowed(self):
        self.disable_rule("block_aml_identity")
        self.call("get_aml_case_summary", {"subject_id": "S17"})
        self.assertTrue(self.call("resolve_aml_subject", {"subject_id": "S17"}).allowed)
        self.assertFalse(self.call("get_customer_contact", {"customer_id": "C17"}).allowed)
        self.assertFalse(self.call("get_customer_workplace", {"customer_id": "C17"}).allowed)
        self.assertTrue(self.call("get_customer_contact", {"customer_id": "C99"}).allowed)

    def test_aml_case_of_a_customer_whose_contact_is_known_is_blocked_even_when_identity_is_allowed(self):
        self.disable_rule("block_aml_identity")
        self.call("get_customer_contact", {"customer_id": "C17"})
        self.call("resolve_aml_subject", {"subject_id": "S17"})
        self.assertFalse(self.call("get_aml_case_summary", {"subject_id": "S17"}).allowed)

    def test_denied_fact_not_persisted_and_harmless_query_works(self):
        self.call("get_aml_case_summary", {"subject_id": "S17"})
        self.call("resolve_aml_subject", {"subject_id": "S17"})
        self.assertFalse(any(f.relation == "identity" for f in
                             self.middleware.store.facts_for_user(self.principal)))
        self.assertTrue(self.call("get_transaction_anomalies", {"subject_id": "S17"}).allowed)

    def test_cross_session_history(self):
        self.call("get_aml_case_summary", {"subject_id": "S17"}, session="one")
        self.assertFalse(self.call("resolve_aml_subject", {"subject_id": "S17"}, session="two").allowed)

    def test_persistent_history_survives_restart(self):
        self.call("get_aml_case_summary", {"subject_id": "S17"})
        self.middleware = PolicyMiddleware(KnowledgeStore(self.db), PolicyConfigStore(self.rules), build_registry())
        self.assertFalse(self.call("resolve_aml_subject", {"subject_id": "S17"}).allowed)

    def test_distinct_user_is_isolated(self):
        self.call("get_aml_case_summary", {"subject_id": "S17"})
        other = demo_principal("bank-a", "employee-2")
        self.assertTrue(self.call("resolve_aml_subject", {"subject_id": "S17"}, principal=other).allowed)

    def test_distinct_tenant_is_isolated(self):
        self.call("get_aml_case_summary", {"subject_id": "S17"})
        other = demo_principal("bank-b", "employee-1")
        self.assertTrue(self.call("resolve_aml_subject", {"subject_id": "S17"}, principal=other).allowed)

    def test_missing_identity_rejected(self):
        request = MCPRequest("x", "s", user_id="employee-1", tool_name="get_aml_case_summary",
                             arguments={"subject_id": "S17"})
        self.assertFalse(self.middleware.handle(request, execute=self.executor).allowed)
        self.assertEqual(self.calls, [])
        with self.assertRaises(PolicyError):
            self.middleware.store.facts_for_user(None)

    def test_spoofed_user_hint_rejected(self):
        request = MCPRequest("x", "s", user_id="other", tool_name="get_aml_case_summary",
                             arguments={"subject_id": "S17"})
        self.assertFalse(self.middleware.handle(request, principal=self.principal,
                                               execute=self.executor).allowed)
        self.assertEqual(self.calls, [])

    def test_model_facts_rejected(self):
        request = MCPRequest("x", "s", tool_name="get_aml_case_summary",
            arguments={"subject_id": "S17"}, facts=[{"relation": "trusted", "value": "true"}])
        self.assertFalse(self.middleware.handle(request, principal=self.principal,
                                               execute=self.executor).allowed)
        self.assertEqual(self.calls, [])
        admin = demo_principal("bank-a", "auditor", "security_admin")
        events = self.middleware.store.audit_events(admin)
        self.assertEqual(events[0]["internal_code"], "untrusted_request_fields")

    def test_retry_cannot_reuse_approved_response_with_injected_facts(self):
        self.call("get_aml_case_summary", {"subject_id": "S17"}, request_id="approved")
        request = MCPRequest("approved", "s", tool_name="get_aml_case_summary",
            arguments={"subject_id": "S17"}, facts=[{"relation": "fake"}])
        reply = self.middleware.handle(request, principal=self.principal, execute=self.executor)
        self.assertFalse(reply.allowed)
        self.assertEqual(len(self.calls), 1)

    def test_unknown_tool_rejected(self):
        self.assertFalse(self.call("run_sql", {"query": "SELECT * FROM clients"}).allowed)
        self.assertEqual(self.calls, [])

    def test_extra_filters_rejected(self):
        self.assertFalse(self.call("get_aml_case_summary", {"subject_id": "S17",
            "customer_id": "C17"}).allowed)
        self.assertEqual(self.calls, [])

    def test_invalid_policy_fails_closed(self):
        for index, content in enumerate(("{}", "{", '{"version":"v","rules":[]}')):
            Path(self.rules).write_text(content)
            self.assertFalse(self.call("get_aml_case_summary", {"subject_id": "S17"},
                                      request_id=f"bad-{index}").allowed)
        self.assertEqual(self.calls, [])

    def test_missing_policy_fails_closed(self):
        Path(self.rules).unlink()
        self.assertFalse(self.call("get_aml_case_summary", {"subject_id": "S17"}).allowed)
        self.assertEqual(self.calls, [])

    def test_indirect_account_path_is_blocked(self):
        self.call("get_aml_case_summary", {"subject_id": "S17"})
        self.assertTrue(self.call("get_blocked_accounts", {"subject_id": "S17"}).allowed)
        self.assertFalse(self.call("get_account_owner", {"account_id": "A9"}).allowed)
        self.assertEqual(len(self.calls), 2)

    def test_known_owners_conservatively_block_unknown_returned_account(self):
        self.call("get_account_owner", {"account_id": "A9"})
        self.call("get_aml_case_summary", {"subject_id": "S17"})
        self.assertFalse(self.call("get_blocked_accounts", {"subject_id": "S17"}).allowed)
        self.assertEqual(len(self.calls), 2)

    def test_true_and_false_review_have_same_refusal(self):
        self.call("resolve_aml_subject", {"subject_id": "S17"})
        replies = []
        for status in (True, False):
            self.review = status
            replies.append(self.call("get_aml_case_summary", {"subject_id": "S17"}).as_dict())
        self.assertEqual(replies[0], replies[1])
        self.assertEqual(len(self.calls), 1)

    def test_literal_status_policy_is_checked_for_all_possible_statuses(self):
        k = lambda *t: DatalogAtom("knows", tuple(t))
        rule = PolicyRuleConfig("only_true", DatalogRule("only_true",
            DatalogAtom("violation", ("?u", "?s", "only_true")),
            (k("?u", "?s", "aml_review", "true"), k("?u", "?s", "identity", "?c"))))
        self.middleware.config_store.save([rule], "literal-policy")
        self.call("resolve_aml_subject", {"subject_id": "S17"})
        self.review = False
        self.assertFalse(self.call("get_aml_case_summary", {"subject_id": "S17"}).allowed)
        self.assertEqual(len(self.calls), 1)

    def test_unexpected_response_fields_never_leak_or_persist(self):
        def bad(tool, args):
            return {**args, "under_review": True, "phone": "secret"}
        reply = self.call("get_aml_case_summary", {"subject_id": "S17"}, execute=bad)
        self.assertFalse(reply.allowed)
        self.assertIsNone(reply.data)
        self.assertEqual(self.middleware.store.facts_for_user(self.principal), [])
        admin = demo_principal("bank-a", "auditor", "security_admin")
        self.assertNotIn("secret", json.dumps(self.middleware.store.audit_events(admin)))

    def test_wrong_entity_response_is_blocked(self):
        reply = self.call("get_aml_case_summary", {"subject_id": "S17"},
                          execute=lambda tool, args: {"subject_id": "S99", "under_review": True})
        self.assertFalse(reply.allowed)
        self.assertEqual(self.middleware.store.facts_for_user(self.principal), [])

    def test_executor_exception_is_generic_and_not_persisted_as_knowledge(self):
        def fail(tool, args):
            raise RuntimeError("secret internal identifier")
        reply = self.call("get_aml_case_summary", {"subject_id": "S17"}, execute=fail)
        self.assertFalse(reply.allowed)
        self.assertNotIn("secret", reply.reason)
        self.assertEqual(self.middleware.store.facts_for_user(self.principal), [])

    def test_same_request_retry_is_idempotent(self):
        first = self.call("get_aml_case_summary", {"subject_id": "S17"}, request_id="retry")
        again = self.call("get_aml_case_summary", {"subject_id": "S17"}, request_id="retry")
        self.assertEqual(first, again)
        self.assertEqual(len(self.calls), 1)

    def test_request_id_reuse_with_different_payload_is_blocked(self):
        self.call("get_aml_case_summary", {"subject_id": "S17"}, request_id="retry")
        self.assertFalse(self.call("resolve_aml_subject", {"subject_id": "S17"}, request_id="retry").allowed)
        self.assertEqual(len(self.calls), 1)

    def test_admin_proof_and_tenant_scoping(self):
        self.call("get_aml_case_summary", {"subject_id": "S17"}, request_id="first")
        self.call("resolve_aml_subject", {"subject_id": "S17"}, request_id="second")
        with self.assertRaises(PolicyError):
            self.middleware.store.audit_events(self.principal)
        admin = demo_principal("bank-a", "auditor", "security_admin")
        events = self.middleware.store.audit_events(admin)
        self.assertEqual(len(events), 2)
        proof = json.loads(events[-1]["evidence_json"])
        self.assertEqual(proof["request_ids"], ["first", "second"])
        self.assertIn("block_aml_identity", proof["rule_ids"])
        self.assertEqual(self.middleware.store.audit_events(
            demo_principal("bank-b", "auditor", "security_admin")), [])

    def test_parallel_complementary_calls_cannot_both_pass(self):
        gate = threading.Barrier(2)
        def run(tool):
            isolated = PolicyMiddleware(KnowledgeStore(self.db), PolicyConfigStore(self.rules), build_registry())
            gate.wait(timeout=5)
            return isolated.handle(MCPRequest(tool, tool, tool_name=tool,
                arguments={"subject_id": "S17"}), principal=self.principal, execute=self.executor)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, tool) for tool in
                       ("get_aml_case_summary", "resolve_aml_subject")]
            replies = [future.result(timeout=10) for future in futures]
        self.assertEqual(sorted(r.outcome for r in replies), ["allow", "block"])
        self.assertEqual(len(self.calls), 1)

    def test_legacy_database_is_not_silently_reset(self):
        path = str(Path(self.temp.name) / "legacy.sqlite")
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE knowledge_facts (subject TEXT)")
            conn.execute("INSERT INTO knowledge_facts VALUES ('existing-history')")
        with self.assertRaises(LegacyDatabaseError):
            KnowledgeStore(path)
        with sqlite3.connect(path) as conn:
            self.assertEqual(conn.execute("SELECT * FROM knowledge_facts").fetchone()[0], "existing-history")


class DatalogTests(unittest.TestCase):
    def test_subject_is_preserved(self):
        atom = KnowledgeFact("S17", "aml_review", "true").to_atom("user")
        self.assertEqual(atom.terms, ("user", "S17", "aml_review", "true"))

    def test_unsafe_rule_is_rejected(self):
        with self.assertRaises(PolicyError):
            DatalogRule("bad", DatalogAtom("violation", ("?u", "?missing", "bad")),
                        (DatalogAtom("knows", ("?u", "?s", "identity", "?c")),))

    def test_wrong_arity_rejected(self):
        with self.assertRaises(PolicyError):
            DatalogAtom("knows", ("user", "aml_flag", "true"))

    def test_fact_variables_rejected(self):
        with self.assertRaises(PolicyError):
            KnowledgeFact("?subject", "identity", "C17")

    def test_inference_limit_fails_instead_of_silently_ignoring_facts(self):
        program = DatalogProgram(max_facts=1)
        with self.assertRaises(InferenceLimit):
            program.infer([DatalogAtom("knows", ("u", "S1", "identity", "C1")),
                           DatalogAtom("knows", ("u", "S2", "identity", "C2"))])

    def test_recursive_cycle_reaches_fixed_point(self):
        k = lambda relation: DatalogAtom("knows", ("?u", "?s", relation, "?v"))
        rules = [DatalogRule("a_to_b", k("b"), (k("a"),)),
                 DatalogRule("b_to_a", k("a"), (k("b"),))]
        facts = DatalogProgram(rules).infer([DatalogAtom("knows", ("u", "S1", "a", "x"))])
        self.assertEqual(len(facts), 2)


if __name__ == "__main__":
    unittest.main()
