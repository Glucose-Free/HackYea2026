"""Stand-in for the black-box data MCP server, so the demo runs end to end before the real one exists."""

import json
import os

from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent

SERVER_NAME = "stub-data"
HOST_ENV = "STUB_MCP_HOST"
PORT_ENV = "STUB_MCP_PORT"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = "8001"
PHONE_NUMBERS_DENIAL = "Denied: phone numbers cannot be combined with transaction history (inference risk)."
CHECKPOINT_STEPS_FIELD = "checkpoint_steps"

TRANSACTIONS = [
    {"id": "TX-1001", "date": "2026-09-28", "amount_pln": 12500.00, "branch": "Warsaw", "flagged": True},
    {"id": "TX-1002", "date": "2026-09-29", "amount_pln": 830.40, "branch": "Krakow", "flagged": False},
    {"id": "TX-1003", "date": "2026-09-30", "amount_pln": 47000.00, "branch": "Warsaw", "flagged": True},
    {"id": "TX-1004", "date": "2026-10-01", "amount_pln": 215.99, "branch": "Gdansk", "flagged": False},
    {"id": "TX-1005", "date": "2026-10-02", "amount_pln": 9100.00, "branch": "Wroclaw", "flagged": False},
]
BRANCH_SUMMARIES = {
    "Warsaw": {"accounts": 18240, "active_loans": 3120, "monthly_volume_pln": 48_200_000},
    "Krakow": {"accounts": 9410, "active_loans": 1480, "monthly_volume_pln": 19_700_000},
    "Gdansk": {"accounts": 6120, "active_loans": 890, "monthly_volume_pln": 11_300_000},
    "Wroclaw": {"accounts": 7800, "active_loans": 1210, "monthly_volume_pln": 15_900_000},
}
SIMULATED_CHECKPOINT_STEPS = [
    {"middleware": "query_allowlist", "outcome": "passed", "reason": "query template get_customer_phone_numbers is allowed"},
    {"middleware": "inference_guard", "outcome": "denied", "reason": "session already fetched flagged transactions; adding phone numbers would identify customers"},
]


def build_stub_server() -> MCPServer:
    server = MCPServer(SERVER_NAME, instructions="Stand-in for the organization's data MCP server, with fake data.")

    @server.tool()
    def list_recent_transactions(limit: int = 5) -> str:
        """List the most recent transactions with id, date, amount, branch and whether they were flagged."""
        return json.dumps(TRANSACTIONS[:max(0, limit)])

    @server.tool()
    def get_branch_summary(branch: str = "Warsaw") -> str:
        """Get account, loan and volume figures for a branch (Warsaw, Krakow, Gdansk, Wroclaw)."""
        return json.dumps({"branch": branch, **BRANCH_SUMMARIES.get(branch, {})})

    @server.tool()
    def get_customer_phone_numbers(transaction_ids: list[str] | None = None) -> CallToolResult:
        """Get customer phone numbers for the given transactions."""
        # Always denied: demonstrates a checkpoint-2 inference refusal and its middleware trace.
        return CallToolResult(
            content=[TextContent(type="text", text=PHONE_NUMBERS_DENIAL)],
            is_error=True,
            structured_content={CHECKPOINT_STEPS_FIELD: SIMULATED_CHECKPOINT_STEPS},
        )

    return server


if __name__ == "__main__":
    build_stub_server().run(
        "streamable-http",
        host=os.environ.get(HOST_ENV, DEFAULT_HOST),
        port=int(os.environ.get(PORT_ENV, DEFAULT_PORT)),
    )
