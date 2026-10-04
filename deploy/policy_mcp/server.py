"""The demo's data MCP server: the Datalog policy engine over the bank demo's five tools.

Run as a module (`python -m deploy.policy_mcp.server`) so the repo root, where the engine lives, is importable.
"""

import os
from pathlib import Path

from examples import bank_demo
from mcp_policy_http_server import PolicyHttpMCPServer, ServedScope
from policy_middleware import KnowledgeStore, PolicyConfigStore, PolicyMiddleware

HOST_ENV = "POLICY_MCP_HOST"
PORT_ENV = "POLICY_MCP_PORT"
DB_PATH_ENV = "POLICY_DB_PATH"
RULES_PATH_ENV = "POLICY_RULES_PATH"
TENANT_ID_ENV = "POLICY_TENANT_ID"
DATASET_ID_ENV = "POLICY_DATASET_ID"
DEMO_DB_PATH_ENV = "POLICY_DEMO_DB_PATH"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = "8001"
DEFAULT_DB_PATH = "/data/policy/knowledge.sqlite3"
DEMO_DB_FILENAME = "bank_demo.sqlite3"
DEFAULT_TENANT_ID = "demo-bank"
DEFAULT_DATASET_ID = "bank-demo-v1"
STREAMABLE_HTTP_TRANSPORT = "streamable-http"
BANK_DEMO_RULES_PATH = str(Path(bank_demo.__file__).with_name("bank_demo_rules.json"))


def resolve_demo_db_path(db_path: str, demo_db_path: str | None = None) -> str:
    if demo_db_path:
        return demo_db_path
    return str(Path(db_path).with_name(DEMO_DB_FILENAME))


def build_policy_data_server(db_path: str, rules_path: str = BANK_DEMO_RULES_PATH,
                             tenant_id: str = DEFAULT_TENANT_ID,
                             dataset_id: str = DEFAULT_DATASET_ID,
                             demo_db_path: str | None = None) -> PolicyHttpMCPServer:
    demo_db_path = resolve_demo_db_path(db_path, demo_db_path)
    bank_demo.initialize_bank_demo_db(demo_db_path)
    middleware = PolicyMiddleware(KnowledgeStore(db_path), PolicyConfigStore(rules_path), bank_demo.build_registry())
    scope = ServedScope(tenant_id, role=bank_demo.ANALYST_ROLE, dataset_id=dataset_id)
    return PolicyHttpMCPServer(middleware, bank_demo.build_executor(demo_db_path), scope)


def main() -> None:
    db_path = os.environ.get(DB_PATH_ENV, DEFAULT_DB_PATH)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    server = build_policy_data_server(
        db_path,
        rules_path=os.environ.get(RULES_PATH_ENV, BANK_DEMO_RULES_PATH),
        tenant_id=os.environ.get(TENANT_ID_ENV, DEFAULT_TENANT_ID),
        dataset_id=os.environ.get(DATASET_ID_ENV, DEFAULT_DATASET_ID),
        demo_db_path=os.environ.get(DEMO_DB_PATH_ENV),
    )
    server.run(STREAMABLE_HTTP_TRANSPORT, host=os.environ.get(HOST_ENV, DEFAULT_HOST),
               port=int(os.environ.get(PORT_ENV, DEFAULT_PORT)))


if __name__ == "__main__":
    main()
