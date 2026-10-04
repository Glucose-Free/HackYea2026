"""The bank demo's database: its schema and the read-only queries behind the data tools.

The connection is opened with SQLite's `mode=ro`, so a query cannot write even if a tool is wrong, and a missing
database file fails closed instead of being silently created empty.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

BRANCHES = ("Warsaw", "Krakow", "Gdansk", "Wroclaw", "Poznan", "Lodz")
FEATURED_AML_CASE_ID = "AML-2026-0042"
MASKED_ACCOUNT_PREFIX = "PL-****-"
# A 3b chat model loses track of long tool results; well under the policy core's row and byte limits.
MAX_RETURNED_ROWS = 50
READ_ONLY_URI_SUFFIX = "?mode=ro"
CONNECT_TIMEOUT_SECONDS = 5.0
NO_SUCH_RECORD_ERROR = "No such record"

SCHEMA = """
CREATE TABLE customers (
    customer_id     TEXT PRIMARY KEY,
    full_name       TEXT NOT NULL,
    segment         TEXT NOT NULL CHECK (segment IN ('retail', 'premium', 'business')),
    branch          TEXT NOT NULL,
    customer_since  TEXT NOT NULL,
    phone           TEXT NOT NULL,
    email           TEXT NOT NULL,
    employer        TEXT NOT NULL,
    work_city       TEXT NOT NULL
);
CREATE TABLE accounts (
    account_number  TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers (customer_id),
    account_type    TEXT NOT NULL,
    opened_on       TEXT NOT NULL,
    blocked_on      TEXT,
    block_reason    TEXT
);
CREATE TABLE transactions (
    transaction_id  TEXT PRIMARY KEY,
    account_number  TEXT NOT NULL REFERENCES accounts (account_number),
    booked_on       TEXT NOT NULL,
    amount_pln      REAL NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    channel         TEXT NOT NULL,
    counterparty    TEXT NOT NULL,
    branch          TEXT NOT NULL,
    anomaly_pattern TEXT
);
CREATE TABLE aml_cases (
    case_id         TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers (customer_id),
    status          TEXT NOT NULL,
    risk_level      TEXT NOT NULL,
    opened_on       TEXT NOT NULL
);
CREATE TABLE aml_case_transactions (
    case_id         TEXT NOT NULL REFERENCES aml_cases (case_id),
    transaction_id  TEXT NOT NULL REFERENCES transactions (transaction_id),
    PRIMARY KEY (case_id, transaction_id)
);
CREATE INDEX transactions_by_anomaly ON transactions (anomaly_pattern, booked_on);
CREATE INDEX accounts_by_customer ON accounts (customer_id);
"""

LIST_CUSTOMERS_QUERY = """
SELECT customer_id, segment, branch, customer_since FROM customers
WHERE (:branch IS NULL OR branch = :branch)
ORDER BY CAST(SUBSTR(customer_id, 6) AS INTEGER) LIMIT :limit
"""
LIST_TRANSACTION_ANOMALIES_QUERY = """
SELECT transaction_id AS id, booked_on AS date, amount_pln, branch, anomaly_pattern AS pattern FROM transactions
WHERE anomaly_pattern IS NOT NULL AND (:branch IS NULL OR branch = :branch)
ORDER BY booked_on DESC, transaction_id LIMIT :limit
"""
LIST_BLOCKED_ACCOUNTS_QUERY = """
SELECT :mask_prefix || SUBSTR(account_number, -4) AS account, blocked_on, block_reason AS reason FROM accounts
WHERE blocked_on IS NOT NULL
ORDER BY blocked_on DESC, account_number LIMIT :limit
"""
LIST_AML_CASES_QUERY = """
SELECT aml_cases.case_id, status, risk_level, opened_on, COUNT(aml_case_transactions.transaction_id) AS linked_transaction_count
FROM aml_cases LEFT JOIN aml_case_transactions USING (case_id)
GROUP BY aml_cases.case_id ORDER BY opened_on DESC, aml_cases.case_id DESC LIMIT :limit
"""
GET_AML_CASE_QUERY = "SELECT case_id, status, risk_level, opened_on, customer_id FROM aml_cases WHERE case_id = :case_id"
LIST_AML_CASE_TRANSACTIONS_QUERY = """
SELECT transaction_id FROM aml_case_transactions WHERE case_id = :case_id ORDER BY transaction_id
"""
GET_CUSTOMER_CONTACT_QUERY = "SELECT customer_id, full_name, phone, email FROM customers WHERE customer_id = :customer_id"
GET_CUSTOMER_WORKPLACE_QUERY = "SELECT customer_id, employer, work_city AS city FROM customers WHERE customer_id = :customer_id"


class BankDemoDatabase:
    def __init__(self, db_path: str | Path):
        self._read_only_uri = Path(db_path).resolve().as_uri() + READ_ONLY_URI_SUFFIX

    def list_customers(self, branch: str | None = None) -> list[dict[str, Any]]:
        return self._fetch_all(LIST_CUSTOMERS_QUERY, {"branch": branch, "limit": MAX_RETURNED_ROWS})

    def list_transaction_anomalies(self, branch: str | None = None) -> list[dict[str, Any]]:
        return self._fetch_all(LIST_TRANSACTION_ANOMALIES_QUERY, {"branch": branch, "limit": MAX_RETURNED_ROWS})

    def list_blocked_accounts(self) -> list[dict[str, Any]]:
        return self._fetch_all(LIST_BLOCKED_ACCOUNTS_QUERY,
                               {"mask_prefix": MASKED_ACCOUNT_PREFIX, "limit": MAX_RETURNED_ROWS})

    def list_aml_cases(self) -> list[dict[str, Any]]:
        return self._fetch_all(LIST_AML_CASES_QUERY, {"limit": MAX_RETURNED_ROWS})

    def get_aml_case(self, case_id: str) -> dict[str, Any]:
        case = self._fetch_one(GET_AML_CASE_QUERY, {"case_id": case_id})
        linked_rows = self._fetch_all(LIST_AML_CASE_TRANSACTIONS_QUERY, {"case_id": case_id})
        return {**case, "linked_transactions": [row["transaction_id"] for row in linked_rows]}

    def get_customer_contact(self, customer_id: str) -> dict[str, Any]:
        return self._fetch_one(GET_CUSTOMER_CONTACT_QUERY, {"customer_id": customer_id})

    def get_customer_workplace(self, customer_id: str) -> dict[str, Any]:
        return self._fetch_one(GET_CUSTOMER_WORKPLACE_QUERY, {"customer_id": customer_id})

    def _fetch_all(self, query: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute(query, parameters)]

    def _fetch_one(self, query: str, parameters: dict[str, Any]) -> dict[str, Any]:
        rows = self._fetch_all(query, parameters)
        if not rows:
            raise LookupError(NO_SUCH_RECORD_ERROR)
        return rows[0]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._read_only_uri, uri=True, timeout=CONNECT_TIMEOUT_SECONDS)
        connection.row_factory = sqlite3.Row
        return connection
