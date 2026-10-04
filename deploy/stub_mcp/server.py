"""Stand-in for the black-box data MCP server, so the demo runs end to end before the real one exists.

Tool names and the AML inference rule mirror the other team's Datalog policy middleware, so swapping in
the real server changes only the compose image. See docs/contracts/data-mcp-server.md.
"""

import json
import os
from collections import defaultdict

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import CallToolResult, TextContent

SERVER_NAME = "stub-data"
HOST_ENV = "STUB_MCP_HOST"
PORT_ENV = "STUB_MCP_PORT"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = "8001"

# Contract keys shared with the gateway (docs/contracts/data-mcp-server.md).
USER_ID_META_KEY = "ai-control-gateway/user_id"
CHECKPOINT_STEPS_FIELD = "checkpoint_steps"
UNKNOWN_USER_ID = "unknown_user"

AML_FLAG_KNOWLEDGE = "aml_flag"
CONTACT_RULE_ID = "aml_contact"
WORKPLACE_RULE_ID = "aml_workplace"
ALLOWLIST_MIDDLEWARE = "tool_allowlist"
POLICY_MIDDLEWARE = "datalog_policy"
ALLOWLIST_PASSED_REASON = "tool {tool_name} is on the allowlist"
POLICY_DENIED_REASON = "{rule_id}: you already know an AML case; adding {knowledge} would identify the customer"
DENIAL_TEXT = "Denied by data policy ({rule_id}): combining AML case knowledge with customer {knowledge} is not allowed."

TRANSACTION_ANOMALIES = [
    {"id": "TX-1001", "date": "2026-09-28", "amount_pln": 12500.00, "branch": "Warsaw", "pattern": "structuring"},
    {"id": "TX-1003", "date": "2026-09-30", "amount_pln": 47000.00, "branch": "Warsaw", "pattern": "rapid in-out"},
]
BLOCKED_ACCOUNTS = [
    {"account": "PL-****-4411", "blocked_on": "2026-09-30", "reason": "suspicious inflows"},
    {"account": "PL-****-9032", "blocked_on": "2026-10-01", "reason": "court order"},
]
AML_CASE_SUMMARY = {
    "case_id": "AML-2026-0042",
    "status": "under investigation",
    "customer_id": "CUST-17",
    "linked_transactions": ["TX-1001", "TX-1003"],
}
CUSTOMER_CONTACTS = {"CUST-17": {"phone": "+48 601 234 567", "email": "j.kowalski@example.pl"}}
CUSTOMER_WORKPLACES = {"CUST-17": {"employer": "Kowalski Logistics sp. z o.o.", "city": "Warsaw"}}


def get_caller_user_id(ctx: Context) -> str:
    meta = ctx.request_context.meta or {}
    return str(meta.get(USER_ID_META_KEY) or UNKNOWN_USER_ID)


def build_data_result(payload: dict) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))])


def build_denial(tool_name: str, rule_id: str, knowledge: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=DENIAL_TEXT.format(rule_id=rule_id, knowledge=knowledge))],
        is_error=True,
        structured_content={CHECKPOINT_STEPS_FIELD: [
            {"middleware": ALLOWLIST_MIDDLEWARE, "outcome": "passed", "reason": ALLOWLIST_PASSED_REASON.format(tool_name=tool_name)},
            {"middleware": POLICY_MIDDLEWARE, "outcome": "denied", "reason": POLICY_DENIED_REASON.format(rule_id=rule_id, knowledge=knowledge)},
        ]},
    )


def build_stub_server() -> MCPServer:
    server = MCPServer(SERVER_NAME, instructions="Stand-in for the organization's data MCP server, with fake data.")
    # Knowledge accumulates per user across chats, like the real Datalog engine; in memory is enough for a stub.
    knowledge_by_user: dict[str, set[str]] = defaultdict(set)

    @server.tool()
    def get_transaction_anomalies() -> str:
        """List recent anomalous transactions (id, date, amount, branch, detected pattern). Anonymized."""
        return json.dumps(TRANSACTION_ANOMALIES)

    @server.tool()
    def get_blocked_accounts() -> str:
        """List recently blocked accounts with masked numbers and the blocking reason. Anonymized."""
        return json.dumps(BLOCKED_ACCOUNTS)

    @server.tool()
    def get_aml_case_summary(ctx: Context) -> str:
        """Get the summary of the current AML (anti-money-laundering) case, including the customer id."""
        knowledge_by_user[get_caller_user_id(ctx)].add(AML_FLAG_KNOWLEDGE)
        return json.dumps(AML_CASE_SUMMARY)

    @server.tool()
    def get_customer_contact(ctx: Context, customer_id: str = "CUST-17") -> CallToolResult:
        """Get a customer's contact details (phone, email) by customer id."""
        if AML_FLAG_KNOWLEDGE in knowledge_by_user[get_caller_user_id(ctx)]:
            return build_denial("get_customer_contact", CONTACT_RULE_ID, "contact details")
        return build_data_result({"customer_id": customer_id, **CUSTOMER_CONTACTS.get(customer_id, {})})

    @server.tool()
    def get_customer_workplace(ctx: Context, customer_id: str = "CUST-17") -> CallToolResult:
        """Get a customer's employer and work location by customer id."""
        if AML_FLAG_KNOWLEDGE in knowledge_by_user[get_caller_user_id(ctx)]:
            return build_denial("get_customer_workplace", WORKPLACE_RULE_ID, "workplace")
        return build_data_result({"customer_id": customer_id, **CUSTOMER_WORKPLACES.get(customer_id, {})})

    return server


if __name__ == "__main__":
    build_stub_server().run(
        "streamable-http",
        host=os.environ.get(HOST_ENV, DEFAULT_HOST),
        port=int(os.environ.get(PORT_ENV, DEFAULT_PORT)),
    )
