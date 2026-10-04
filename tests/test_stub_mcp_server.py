from deploy.stub_mcp.server import build_stub_server
from gateway.agent.tools import McpToolProvider

TRACEPARENT = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"


async def test_stub_server_offers_three_tools_and_denies_phone_numbers():
    async with McpToolProvider(build_stub_server()).open_session() as session:
        tool_names = {tool.name for tool in await session.list_tools()}
        transactions = await session.call_tool("list_recent_transactions", {}, TRACEPARENT)
        phones = await session.call_tool("get_customer_phone_numbers", {}, TRACEPARENT)

    assert tool_names == {"list_recent_transactions", "get_branch_summary", "get_customer_phone_numbers"}
    assert not transactions.denied and "TX-1001" in transactions.content
    assert phones.denied
    assert [step.outcome for step in phones.checkpoint_steps] == ["passed", "denied"]
