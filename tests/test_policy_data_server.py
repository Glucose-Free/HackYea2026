import pytest

from deploy.policy_mcp.server import build_policy_data_server
from gateway.agent.tools import McpToolProvider, ToolCallContext, ToolCaller, ToolCallOutcome

TRACEPARENT = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
ALICE_CONTEXT = ToolCallContext(TRACEPARENT, ToolCaller("u-alice", "chat-1"))
ALICE_LATER_CONTEXT = ToolCallContext(TRACEPARENT, ToolCaller("u-alice", "chat-2"))
BOB_CONTEXT = ToolCallContext(TRACEPARENT, ToolCaller("u-bob", "chat-3"))
ANONYMOUS_CONTEXT = ToolCallContext(TRACEPARENT, ToolCaller("", "chat-4"))
CONTACT_ARGUMENTS = {"customer_id": "CUST-17"}


@pytest.fixture
def server(tmp_path):
    return build_policy_data_server(db_path=str(tmp_path / "knowledge.sqlite3"))


async def test_lists_only_the_five_data_tools(server):
    async with McpToolProvider(server).open_session() as session:
        tool_names = {tool.name for tool in await session.list_tools()}
    assert tool_names == {
        "get_transaction_anomalies",
        "get_blocked_accounts",
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


async def test_knowledge_survives_a_server_restart(tmp_path):
    db_path = str(tmp_path / "knowledge.sqlite3")
    async with McpToolProvider(build_policy_data_server(db_path=db_path)).open_session() as session:
        await session.call_tool("get_aml_case_summary", {}, ALICE_CONTEXT)
    async with McpToolProvider(build_policy_data_server(db_path=db_path)).open_session() as session:
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
