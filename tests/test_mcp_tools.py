from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent

from gateway.agent.tools import CHECKPOINT_STEPS_FIELD, TRACEPARENT_META_KEY, McpToolProvider


def build_test_server(received_meta: list) -> MCPServer:
    server = MCPServer("test-data")

    @server.tool()
    def list_transactions(limit: int = 2) -> str:
        """List recent transactions."""
        return '[{"id": "t1"}]'

    @server.tool()
    def get_phone_numbers() -> CallToolResult:
        """Get phone numbers."""
        return CallToolResult(
            content=[TextContent(type="text", text="denied: combining these queries enables inference")],
            is_error=True,
            structured_content={CHECKPOINT_STEPS_FIELD: [
                {"middleware": "query_allowlist", "outcome": "passed", "reason": "template allowed"},
                {"middleware": "inference_guard", "outcome": "denied", "reason": "joins dates with phones"},
                "malformed entry",
            ]},
        )

    return server


async def test_lists_tools_with_schemas():
    async with McpToolProvider(build_test_server([])).open_session() as session:
        tools = await session.list_tools()
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {"list_transactions", "get_phone_numbers"}
    assert by_name["list_transactions"].description == "List recent transactions."
    assert "limit" in by_name["list_transactions"].input_schema["properties"]


async def test_passed_call_returns_text_content():
    async with McpToolProvider(build_test_server([])).open_session() as session:
        result = await session.call_tool("list_transactions", {"limit": 1}, "00-" + "a" * 32 + "-" + "b" * 16 + "-01")
    assert not result.denied
    assert result.content == '[{"id": "t1"}]'
    assert result.checkpoint_steps == ()


async def test_denied_call_returns_refusal_text_and_checkpoint_steps():
    async with McpToolProvider(build_test_server([])).open_session() as session:
        result = await session.call_tool("get_phone_numbers", {}, "00-" + "a" * 32 + "-" + "b" * 16 + "-01")
    assert result.denied
    assert "inference" in result.content
    assert [(step.middleware, step.outcome) for step in result.checkpoint_steps] == [
        ("query_allowlist", "passed"),
        ("inference_guard", "denied"),
    ]


def test_traceparent_meta_key_is_w3c_name():
    assert TRACEPARENT_META_KEY == "traceparent"
