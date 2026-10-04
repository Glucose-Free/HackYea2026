"""The demo's data MCP server: the Datalog policy engine over the bank demo's data tools and database.

Run as a module (`python -m deploy.policy_mcp.server`) so the repo root, where the engine lives, is importable.
"""

import os
from pathlib import Path

from examples import bank_demo
from examples.bank_demo_seed import ensure_bank_demo_database
from mcp_policy_http_server import PolicyHttpMCPServer, ServedScope
from policy_middleware import KnowledgeStore, PolicyConfigStore, PolicyMiddleware

HOST_ENV = "POLICY_MCP_HOST"
PORT_ENV = "POLICY_MCP_PORT"
DB_PATH_ENV = "POLICY_DB_PATH"
RULES_PATH_ENV = "POLICY_RULES_PATH"
BANK_DB_PATH_ENV = "BANK_DB_PATH"
TENANT_ID_ENV = "POLICY_TENANT_ID"
DATASET_ID_ENV = "POLICY_DATASET_ID"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = "8001"
DEFAULT_DB_PATH = "/data/policy/knowledge.sqlite3"
DEFAULT_BANK_DB_PATH = "/data/bank/bank_demo.sqlite3"
DEFAULT_TENANT_ID = "demo-bank"
# Part of every knowledge scope: bumped when the demo data changed, so facts learned about the old data do not apply.
DEFAULT_DATASET_ID = "bank-demo-v2"
STREAMABLE_HTTP_TRANSPORT = "streamable-http"
BANK_DEMO_RULES_PATH = str(Path(bank_demo.__file__).with_name("bank_demo_rules.json"))


def build_policy_data_server(db_path: str, bank_db_path: str = DEFAULT_BANK_DB_PATH,
                             rules_path: str = BANK_DEMO_RULES_PATH, tenant_id: str = DEFAULT_TENANT_ID,
                             dataset_id: str = DEFAULT_DATASET_ID) -> PolicyHttpMCPServer:
    middleware = PolicyMiddleware(KnowledgeStore(db_path), PolicyConfigStore(rules_path), bank_demo.build_registry())
    scope = ServedScope(tenant_id, role=bank_demo.ANALYST_ROLE, dataset_id=dataset_id)
    return PolicyHttpMCPServer(middleware, bank_demo.build_executor(bank_db_path), scope)


def ensure_policy_rules(rules_path: str) -> None:
    """Seeds the editable rules file from the shipped defaults; an existing file, edited or not, is kept."""
    default_version, default_rules = PolicyConfigStore(BANK_DEMO_RULES_PATH).load()
    PolicyConfigStore(rules_path).initialize(list(default_rules), default_version)


def main() -> None:
    db_path = os.environ.get(DB_PATH_ENV, DEFAULT_DB_PATH)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    bank_db_path = os.environ.get(BANK_DB_PATH_ENV, DEFAULT_BANK_DB_PATH)
    ensure_bank_demo_database(bank_db_path)
    rules_path = os.environ.get(RULES_PATH_ENV, BANK_DEMO_RULES_PATH)
    ensure_policy_rules(rules_path)
    server = build_policy_data_server(
        db_path,
        bank_db_path=bank_db_path,
        rules_path=rules_path,
        tenant_id=os.environ.get(TENANT_ID_ENV, DEFAULT_TENANT_ID),
        dataset_id=os.environ.get(DATASET_ID_ENV, DEFAULT_DATASET_ID),
    )
    server.run(STREAMABLE_HTTP_TRANSPORT, host=os.environ.get(HOST_ENV, DEFAULT_HOST),
               port=int(os.environ.get(PORT_ENV, DEFAULT_PORT)))


if __name__ == "__main__":
    main()
