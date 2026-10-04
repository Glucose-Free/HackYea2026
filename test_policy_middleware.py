import os
import tempfile
import unittest

from policy_middleware import KnowledgeStore, MCPRequest, PolicyConfigStore, PolicyMiddleware


class PolicyMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "knowledge.sqlite3")
        self.rules_path = os.path.join(self.tempdir.name, "policy_rules.json")
        self.store = KnowledgeStore(db_path=self.db_path)
        self.config = PolicyConfigStore(path=self.rules_path)
        self.middleware = PolicyMiddleware(store=self.store, config_store=self.config)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _learn_aml(self, user_id: str, session_id: str = "s1") -> None:
        self.middleware.handle(
            MCPRequest(
                request_id=f"req_{user_id}_aml",
                session_id=session_id,
                user_id=user_id,
                request_text="show AML case summary",
                tool_name="get_aml_case_summary",
            )
        )

    def test_contact_rule_blocks_after_aml_knowledge(self) -> None:
        self._learn_aml("user_1", session_id="aml_session")

        decision = self.middleware.handle(
            MCPRequest(
                request_id="req_contact",
                session_id="contact_session",
                user_id="user_1",
                request_text="give me the phone number",
                tool_name="get_customer_contact",
                arguments={"customer_id": "cust_1"},
            )
        )

        self.assertEqual(decision.outcome, "block")
        self.assertIn("aml_contact", decision.reason)

    def test_workplace_rule_blocks_after_aml_knowledge(self) -> None:
        self._learn_aml("user_2", session_id="aml_session")

        decision = self.middleware.handle(
            MCPRequest(
                request_id="req_workplace",
                session_id="workplace_session",
                user_id="user_2",
                request_text="give me the employer details",
                tool_name="get_customer_workplace",
                arguments={"customer_id": "cust_1"},
            )
        )

        self.assertEqual(decision.outcome, "block")
        self.assertIn("aml_workplace", decision.reason)

    def test_disabling_contact_rule_keeps_workplace_rule_active(self) -> None:
        self.config.set_rule_enabled("block_aml_contact", False)
        self.middleware.reload_rules()

        self._learn_aml("user_3", session_id="aml_session")

        contact_decision = self.middleware.handle(
            MCPRequest(
                request_id="req_contact_disabled",
                session_id="contact_session",
                user_id="user_3",
                request_text="give me the phone number",
                tool_name="get_customer_contact",
                arguments={"customer_id": "cust_1"},
            )
        )
        workplace_decision = self.middleware.handle(
            MCPRequest(
                request_id="req_workplace_enabled",
                session_id="workplace_session",
                user_id="user_3",
                request_text="give me the employer details",
                tool_name="get_customer_workplace",
                arguments={"customer_id": "cust_1"},
            )
        )

        self.assertEqual(contact_decision.outcome, "allow")
        self.assertEqual(workplace_decision.outcome, "block")
        self.assertIn("aml_workplace", workplace_decision.reason)

    def test_single_information_is_allowed_without_conflict(self) -> None:
        cases = [
            {
                "name": "contact only",
                "user_id": "user_contact",
                "session_id": "contact_session",
                "request_id": "req_contact_only",
                "tool_name": "get_customer_contact",
                "request_text": "give me the phone number",
                "arguments": {"customer_id": "cust_1"},
            },
            {
                "name": "workplace only",
                "user_id": "user_workplace",
                "session_id": "workplace_session",
                "request_id": "req_workplace_only",
                "tool_name": "get_customer_workplace",
                "request_text": "give me the employer details",
                "arguments": {"customer_id": "cust_1"},
            },
            {
                "name": "aml only",
                "user_id": "user_aml",
                "session_id": "aml_session",
                "request_id": "req_aml_only",
                "tool_name": "get_aml_case_summary",
                "request_text": "show AML case summary",
                "arguments": {},
            },
        ]

        for case in cases:
            with self.subTest(case=case["name"]):
                decision = self.middleware.handle(
                    MCPRequest(
                        request_id=case["request_id"],
                        session_id=case["session_id"],
                        user_id=case["user_id"],
                        request_text=case["request_text"],
                        tool_name=case["tool_name"],
                        arguments=case["arguments"],
                    )
                )

                self.assertEqual(decision.outcome, "allow")
                self.assertIn("datalog_allow", decision.tags)


if __name__ == "__main__":
    unittest.main()
