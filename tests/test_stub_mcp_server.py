from deploy.stub_mcp.server import build_stub_server
from gateway.agent.tools import McpToolProvider, ToolCallContext, ToolCaller

TRACEPARENT = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
ALICE_CONTEXT = ToolCallContext(TRACEPARENT, ToolCaller("u-alice", "chat-1"))
ALICE_LATER_CONTEXT = ToolCallContext(TRACEPARENT, ToolCaller("u-alice", "chat-2"))
BOB_CONTEXT = ToolCallContext(TRACEPARENT, ToolCaller("u-bob", "chat-3"))


async def test_stub_server_offers_the_data_tools_named_by_the_policy_engine():
    async with McpToolProvider(build_stub_server()).open_session() as session:
        tool_names = {tool.name for tool in await session.list_tools()}
    assert tool_names == {
        "get_transaction_anomalies",
        "get_blocked_accounts",
        "get_aml_case_summary",
        "get_customer_contact",
        "get_customer_workplace",
    }


async def test_contact_is_allowed_until_the_same_user_learns_an_aml_case_even_in_another_chat():
    async with McpToolProvider(build_stub_server()).open_session() as session:
        before = await session.call_tool("get_customer_contact", {"customer_id": "CUST-17"}, ALICE_CONTEXT)
        aml_summary = await session.call_tool("get_aml_case_summary", {}, ALICE_CONTEXT)
        after = await session.call_tool("get_customer_contact", {"customer_id": "CUST-17"}, ALICE_LATER_CONTEXT)
        workplace = await session.call_tool("get_customer_workplace", {"customer_id": "CUST-17"}, ALICE_LATER_CONTEXT)

    assert not before.denied and "+48" in before.content
    assert not aml_summary.denied and "AML-2026" in aml_summary.content
    assert after.denied and "aml_contact" in after.content
    assert [(step.middleware, step.outcome) for step in after.checkpoint_steps] == [
        ("tool_allowlist", "passed"),
        ("datalog_policy", "denied"),
    ]
    assert workplace.denied and "aml_workplace" in workplace.content


async def test_one_users_aml_knowledge_does_not_block_another_user():
    async with McpToolProvider(build_stub_server()).open_session() as session:
        await session.call_tool("get_aml_case_summary", {}, ALICE_CONTEXT)
        bob_contact = await session.call_tool("get_customer_contact", {"customer_id": "CUST-17"}, BOB_CONTEXT)
    assert not bob_contact.denied
