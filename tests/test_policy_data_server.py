import json

import pytest

from deploy.policy_mcp.server import build_policy_data_server
from examples.bank_demo_seed import create_bank_demo_database
from gateway.agent.chat_model import StubChatModel
from gateway.agent.tools import McpToolProvider, ToolCallContext, ToolCaller, ToolCallOutcome

TRACEPARENT = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
ALICE_CONTEXT = ToolCallContext(TRACEPARENT, ToolCaller("u-alice", "chat-1"))
ALICE_LATER_CONTEXT = ToolCallContext(TRACEPARENT, ToolCaller("u-alice", "chat-2"))
BOB_CONTEXT = ToolCallContext(TRACEPARENT, ToolCaller("u-bob", "chat-3"))
ANONYMOUS_CONTEXT = ToolCallContext(TRACEPARENT, ToolCaller("", "chat-4"))
CONTACT_ARGUMENTS = {"customer_id": "CUST-17"}


@pytest.fixture(scope="module")
def bank_db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("bank") / "bank_demo.sqlite3"
    create_bank_demo_database(path)
    return str(path)


@pytest.fixture
def server(tmp_path, bank_db_path):
    return build_policy_data_server(db_path=str(tmp_path / "knowledge.sqlite3"), bank_db_path=bank_db_path)


async def test_lists_only_the_data_tools(server):
    async with McpToolProvider(server).open_session() as session:
        tool_names = {tool.name for tool in await session.list_tools()}
    assert tool_names == {
        "list_customers",
        "get_transaction_anomalies",
        "get_blocked_accounts",
        "list_aml_cases",
        "get_aml_case_summary",
        "get_customer_contact",
        "get_customer_workplace",
        "list_transactions",
        "get_customer_profile",
        "list_customer_transactions",
        "get_transaction_details",
        "get_statistics",
    }


async def test_anonymized_tools_answer_without_arguments(server):
    async with McpToolProvider(server).open_session() as session:
        anomalies = await session.call_tool("get_transaction_anomalies", {}, ALICE_CONTEXT)
        blocked = await session.call_tool("get_blocked_accounts", {}, ALICE_CONTEXT)
    assert not anomalies.denied and "TX-1001" in anomalies.content
    assert not blocked.denied and "PL-****-4411" in blocked.content


async def test_contact_is_denied_once_the_user_knows_the_aml_case_even_in_another_chat(server):
    async with McpToolProvider(server).open_session() as session:
        aml_summary = await session.call_tool("get_aml_case_summary", {}, ALICE_CONTEXT)
        contact = await session.call_tool("get_customer_contact", CONTACT_ARGUMENTS, ALICE_LATER_CONTEXT)
        workplace = await session.call_tool("get_customer_workplace", CONTACT_ARGUMENTS, ALICE_LATER_CONTEXT)

    assert not aml_summary.denied and "AML-2026-0042" in aml_summary.content
    assert contact.denied and "aml_contact" in contact.content and "+48" not in contact.content
    assert [(step.middleware, step.outcome) for step in contact.checkpoint_steps] == [
        ("tool_permission", "passed"),
        ("datalog_policy", "denied"),
    ]
    assert "aml_contact" in contact.checkpoint_steps[-1].reason
    assert workplace.denied and "aml_workplace" in workplace.content


async def test_aml_case_is_denied_once_the_user_knows_the_customer_contact(server):
    async with McpToolProvider(server).open_session() as session:
        contact = await session.call_tool("get_customer_contact", CONTACT_ARGUMENTS, ALICE_CONTEXT)
        aml_summary = await session.call_tool("get_aml_case_summary", {}, ALICE_CONTEXT)

    assert not contact.denied and "+48" in contact.content
    assert aml_summary.denied and "aml_contact" in aml_summary.content
    assert "AML-2026-0042" not in aml_summary.content


async def test_one_users_knowledge_does_not_block_another_user(server):
    async with McpToolProvider(server).open_session() as session:
        await session.call_tool("get_aml_case_summary", {}, ALICE_CONTEXT)
        bob_contact = await session.call_tool("get_customer_contact", CONTACT_ARGUMENTS, BOB_CONTEXT)
    assert not bob_contact.denied


async def test_knowledge_survives_a_server_restart(tmp_path, bank_db_path):
    db_path = str(tmp_path / "knowledge.sqlite3")
    async with McpToolProvider(build_policy_data_server(db_path, bank_db_path)).open_session() as session:
        await session.call_tool("get_aml_case_summary", {}, ALICE_CONTEXT)
    async with McpToolProvider(build_policy_data_server(db_path, bank_db_path)).open_session() as session:
        contact = await session.call_tool("get_customer_contact", CONTACT_ARGUMENTS, ALICE_CONTEXT)
    assert contact.denied


async def test_call_without_a_user_is_denied_and_adds_no_knowledge(server):
    async with McpToolProvider(server).open_session() as session:
        anonymous = await session.call_tool("get_aml_case_summary", {}, ANONYMOUS_CONTEXT)
        anonymous_again = await session.call_tool("get_customer_contact", CONTACT_ARGUMENTS, ANONYMOUS_CONTEXT)
        alice_contact = await session.call_tool("get_customer_contact", CONTACT_ARGUMENTS, ALICE_CONTEXT)

    assert anonymous.denied and "AML-2026-0042" not in anonymous.content
    assert [(step.middleware, step.outcome) for step in anonymous.checkpoint_steps] == [("caller_identity", "denied")]
    assert anonymous_again.denied
    assert not alice_contact.denied


async def test_invalid_arguments_fail_without_counting_as_a_policy_denial(server):
    async with McpToolProvider(server).open_session() as session:
        result = await session.call_tool("get_customer_contact", {"customer_id": "'; DROP TABLE x"}, ALICE_CONTEXT)
    assert result.outcome is ToolCallOutcome.FAILED
    assert result.content
    assert result.checkpoint_steps == ()


async def test_listing_tools_name_no_customer_and_mask_account_numbers(server):
    async with McpToolProvider(server).open_session() as session:
        customers = await session.call_tool("list_customers", {"branch": "Krakow"}, ALICE_CONTEXT)
        anomalies = await session.call_tool("get_transaction_anomalies", {"branch": "Gdansk"}, ALICE_CONTEXT)
        blocked = await session.call_tool("get_blocked_accounts", {}, ALICE_CONTEXT)
        aml_cases = await session.call_tool("list_aml_cases", {}, ALICE_CONTEXT)
        contact_afterwards = await session.call_tool("get_customer_contact", CONTACT_ARGUMENTS, ALICE_CONTEXT)

    customer_rows = json.loads(customers.content)
    assert customer_rows and {row["branch"] for row in customer_rows} == {"Krakow"}
    assert not any(key in row for row in customer_rows for key in ("full_name", "phone", "email"))
    assert {row["branch"] for row in json.loads(anomalies.content)} == {"Gdansk"}
    assert all(row["account"].startswith("PL-****-") and len(row["account"]) == 12
               for row in json.loads(blocked.content))
    aml_rows = json.loads(aml_cases.content)
    assert "AML-2026-0042" in {row["case_id"] for row in aml_rows}
    assert "CUST-" not in aml_cases.content
    # The listings disclosed no facts, so they do not hold back a later contact lookup.
    assert not contact_afterwards.denied


async def test_aml_case_summary_by_case_id(server):
    async with McpToolProvider(server).open_session() as session:
        aml_summary = await session.call_tool("get_aml_case_summary", {"case_id": "AML-2026-0007"}, ALICE_CONTEXT)
    case = json.loads(aml_summary.content)
    assert case["case_id"] == "AML-2026-0007" and case["customer_id"].startswith("CUST-")


async def test_unknown_records_fail_without_counting_as_a_policy_denial(server):
    async with McpToolProvider(server).open_session() as session:
        unknown_customer = await session.call_tool("get_customer_contact", {"customer_id": "CUST-99999"}, ALICE_CONTEXT)
        unknown_case = await session.call_tool("get_aml_case_summary", {"case_id": "AML-1999-0001"}, ALICE_CONTEXT)
        unknown_branch = await session.call_tool("list_customers", {"branch": "Atlantis"}, ALICE_CONTEXT)
    for result in (unknown_customer, unknown_case, unknown_branch):
        assert result.outcome is ToolCallOutcome.FAILED and result.checkpoint_steps == ()


STUB_PROMPT_ROUTES = [
    ("Show me the AML case summary", "get_aml_case_summary", {}),
    ("Show me the AML case summary for AML-2026-0007", "get_aml_case_summary", {"case_id": "AML-2026-0007"}),
    ("List AML cases", "list_aml_cases", {}),
    ("List customers", "list_customers", {}),
    ("Give me the customer contact for CUST-17", "get_customer_contact", {"customer_id": "CUST-17"}),
    ("Get the customer workplace for CUST-54", "get_customer_workplace", {"customer_id": "CUST-54"}),
    ("Show me transaction anomalies", "get_transaction_anomalies", {}),
    ("Show me recent transactions", "list_transactions", {}),
    ("List transactions", "list_transactions", {}),
    ("Show me blocked accounts", "get_blocked_accounts", {}),
    ("Show me the customer profile for CUST-53", "get_customer_profile", {"customer_id": "CUST-53"}),
    ("Show me customer transactions for CUST-53", "list_customer_transactions", {"customer_id": "CUST-53"}),
    ("Show me transaction details for TX-1001", "get_transaction_details", {"transaction_id": "TX-1001"}),
    ("Show me bank statistics", "get_statistics", {}),
]


@pytest.mark.parametrize(("prompt", "expected_tool", "expected_arguments"), STUB_PROMPT_ROUTES)
async def test_stub_model_routes_the_documented_prompts(server, prompt, expected_tool, expected_arguments):
    async with McpToolProvider(server).open_session() as session:
        tools = await session.list_tools()
    reply = await StubChatModel().complete([{"role": "user", "content": prompt}], tools)
    assert [(call.name, call.arguments) for call in reply.tool_calls] == [(expected_tool, expected_arguments)]


async def test_filters_narrow_the_listings(server):
    async with McpToolProvider(server).open_session() as session:
        premium = await session.call_tool("list_customers", {"segment": "premium", "branch": "Warsaw"}, ALICE_CONTEXT)
        structuring = await session.call_tool(
            "get_transaction_anomalies",
            {"pattern": "structuring", "date_from": "2026-09-01", "date_to": "2026-09-30"}, ALICE_CONTEXT)
        high_risk = await session.call_tool("list_aml_cases", {"risk_level": "high"}, ALICE_CONTEXT)
        largest = await session.call_tool(
            "list_transactions", {"direction": "out", "min_amount_pln": 50000, "order_by": "largest"}, ALICE_CONTEXT)

    assert {(row["segment"], row["branch"]) for row in json.loads(premium.content)} == {("premium", "Warsaw")}
    structuring_rows = json.loads(structuring.content)
    assert "TX-1001" in {row["id"] for row in structuring_rows}
    assert {row["pattern"] for row in structuring_rows} == {"structuring"}
    assert all("2026-09-01" <= row["date"] <= "2026-09-30" for row in structuring_rows)
    assert {row["risk_level"] for row in json.loads(high_risk.content)} == {"high"}
    amounts = [row["amount_pln"] for row in json.loads(largest.content)]
    assert amounts and amounts == sorted(amounts, reverse=True) and min(amounts) >= 50000
    assert {row["direction"] for row in json.loads(largest.content)} == {"out"}


async def test_a_null_optional_filter_counts_as_left_out(server):
    async with McpToolProvider(server).open_session() as session:
        statistics = await session.call_tool("get_statistics", {"branch": None}, ALICE_CONTEXT)
    assert statistics.outcome is ToolCallOutcome.PASSED and json.loads(statistics.content)["branch"] == "all"


async def test_transaction_tools_name_no_account_or_customer(server):
    async with McpToolProvider(server).open_session() as session:
        details = await session.call_tool("get_transaction_details", {"transaction_id": "TX-1001"}, ALICE_CONTEXT)
        listing = await session.call_tool("list_transactions", {"branch": "Warsaw"}, ALICE_CONTEXT)
        contact_afterwards = await session.call_tool("get_customer_contact", CONTACT_ARGUMENTS, ALICE_CONTEXT)

    detail_record = json.loads(details.content)
    assert detail_record["pattern"] == "structuring"
    for record in (detail_record, *json.loads(listing.content)):
        assert not {"account", "account_number", "customer_id"} & record.keys()
    assert not contact_afterwards.denied


async def test_customer_profile_names_no_account_and_records_no_fact(server):
    async with McpToolProvider(server).open_session() as session:
        await session.call_tool("get_aml_case_summary", {}, ALICE_CONTEXT)
        profile = await session.call_tool("get_customer_profile", CONTACT_ARGUMENTS, ALICE_CONTEXT)

    assert not profile.denied
    record = json.loads(profile.content)
    assert record["customer_id"] == "CUST-17" and record["transaction_count"] >= 4
    # Account numbers or block status would join to the blocked-accounts listing and its AML-related reasons.
    assert all(set(account) == {"account_type", "opened_on"} for account in record["accounts"])
    assert "4411" not in profile.content and "blocked" not in profile.content


async def test_transaction_history_and_identity_cannot_be_combined(server):
    async with McpToolProvider(server).open_session() as session:
        history = await session.call_tool("list_customer_transactions", {"customer_id": "CUST-53"}, ALICE_CONTEXT)
        contact = await session.call_tool("get_customer_contact", {"customer_id": "CUST-53"}, ALICE_CONTEXT)
        workplace = await session.call_tool("get_customer_workplace", {"customer_id": "CUST-53"}, ALICE_CONTEXT)
        other_contact = await session.call_tool("get_customer_contact", {"customer_id": "CUST-1"}, ALICE_CONTEXT)
        bob_contact = await session.call_tool("get_customer_contact", {"customer_id": "CUST-2"}, BOB_CONTEXT)
        bob_history = await session.call_tool("list_customer_transactions", {"customer_id": "CUST-2"}, BOB_CONTEXT)

    assert not history.denied and json.loads(history.content)
    assert contact.denied and "history_contact" in contact.content
    assert workplace.denied and "history_workplace" in workplace.content
    assert not other_contact.denied
    assert bob_history.denied and "history_contact" in bob_history.content


async def test_statistics_cover_the_whole_bank_or_one_branch(server):
    async with McpToolProvider(server).open_session() as session:
        bank = json.loads((await session.call_tool("get_statistics", {}, ALICE_CONTEXT)).content)
        krakow = json.loads((await session.call_tool("get_statistics", {"branch": "Krakow"}, ALICE_CONTEXT)).content)

    assert bank["branch"] == "all" and sum(bank["customers_by_branch"].values()) == 240
    assert sum(bank["aml_cases_by_status"].values()) == 42
    assert sum(bank["blocked_accounts_by_reason"].values()) == 20
    assert krakow["customers_by_branch"].keys() == {"Krakow"}
    assert sum(krakow["aml_cases_by_risk_level"].values()) < 42


@pytest.mark.parametrize(("tool_name", "arguments"), [
    ("list_transactions", {"min_amount_pln": -1}),
    ("list_transactions", {"min_amount_pln": True}),
    ("list_transactions", {"channel": "wire"}),
    ("list_transactions", {"date_from": "2026-10-01", "date_to": "2026-09-01"}),
    ("get_transaction_anomalies", {"date_from": "2026-02-30"}),
    ("list_aml_cases", {"status": "pending"}),
    ("get_transaction_details", {"transaction_id": "CUST-17"}),
    ("get_transaction_details", {"transaction_id": "TX-99999999"}),
    ("list_customer_transactions", {"customer_id": "CUST-99999"}),
])
async def test_bad_filters_and_unknown_records_fail_without_a_policy_denial(server, tool_name, arguments):
    async with McpToolProvider(server).open_session() as session:
        result = await session.call_tool(tool_name, arguments, ALICE_CONTEXT)
    assert result.outcome is ToolCallOutcome.FAILED and result.checkpoint_steps == ()
