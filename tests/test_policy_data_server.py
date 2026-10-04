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
    ("Show me recent transactions", "get_transaction_anomalies", {}),
    ("Show me blocked accounts", "get_blocked_accounts", {}),
]


@pytest.mark.parametrize(("prompt", "expected_tool", "expected_arguments"), STUB_PROMPT_ROUTES)
async def test_stub_model_routes_the_documented_prompts(server, prompt, expected_tool, expected_arguments):
    async with McpToolProvider(server).open_session() as session:
        tools = await session.list_tools()
    reply = await StubChatModel().complete([{"role": "user", "content": prompt}], tools)
    assert [(call.name, call.arguments) for call in reply.tool_calls] == [(expected_tool, expected_arguments)]
