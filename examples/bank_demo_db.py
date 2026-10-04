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
NEWEST_FIRST = "newest"
LARGEST_FIRST = "largest"
TRANSACTION_ORDERS = (NEWEST_FIRST, LARGEST_FIRST)
ALL_BRANCHES = "all"

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
WHERE (:branch IS NULL OR branch = :branch) AND (:segment IS NULL OR segment = :segment)
ORDER BY CAST(SUBSTR(customer_id, 6) AS INTEGER) LIMIT :limit
"""
LIST_TRANSACTION_ANOMALIES_QUERY = """
SELECT transaction_id AS id, booked_on AS date, amount_pln, branch, anomaly_pattern AS pattern FROM transactions
WHERE anomaly_pattern IS NOT NULL AND (:branch IS NULL OR branch = :branch)
  AND (:pattern IS NULL OR anomaly_pattern = :pattern)
  AND (:date_from IS NULL OR booked_on >= :date_from) AND (:date_to IS NULL OR booked_on <= :date_to)
ORDER BY booked_on DESC, transaction_id LIMIT :limit
"""
# No account or customer column: a transaction listing must not become a join key from a customer to an anomaly.
LIST_TRANSACTIONS_QUERY = """
SELECT transaction_id AS id, booked_on AS date, amount_pln, direction, channel, counterparty, branch,
       anomaly_pattern AS pattern
FROM transactions
WHERE (:branch IS NULL OR branch = :branch) AND (:channel IS NULL OR channel = :channel)
  AND (:direction IS NULL OR direction = :direction) AND (:min_amount_pln IS NULL OR amount_pln >= :min_amount_pln)
  AND (:date_from IS NULL OR booked_on >= :date_from) AND (:date_to IS NULL OR booked_on <= :date_to)
ORDER BY CASE WHEN :order_by = :largest_first THEN amount_pln END DESC, booked_on DESC, transaction_id
LIMIT :limit
"""
GET_TRANSACTION_DETAILS_QUERY = """
SELECT transaction_id AS id, booked_on AS date, amount_pln, direction, channel, counterparty, branch,
       anomaly_pattern AS pattern
FROM transactions WHERE transaction_id = :transaction_id
"""
LIST_BLOCKED_ACCOUNTS_QUERY = """
SELECT :mask_prefix || SUBSTR(account_number, -4) AS account, blocked_on, block_reason AS reason FROM accounts
WHERE blocked_on IS NOT NULL
ORDER BY blocked_on DESC, account_number LIMIT :limit
"""
LIST_AML_CASES_QUERY = """
SELECT aml_cases.case_id, status, risk_level, opened_on, COUNT(aml_case_transactions.transaction_id) AS linked_transaction_count
FROM aml_cases LEFT JOIN aml_case_transactions USING (case_id)
WHERE (:status IS NULL OR status = :status) AND (:risk_level IS NULL OR risk_level = :risk_level)
GROUP BY aml_cases.case_id ORDER BY opened_on DESC, aml_cases.case_id DESC LIMIT :limit
"""
GET_AML_CASE_QUERY = "SELECT case_id, status, risk_level, opened_on, customer_id FROM aml_cases WHERE case_id = :case_id"
LIST_AML_CASE_TRANSACTIONS_QUERY = """
SELECT transaction_id FROM aml_case_transactions WHERE case_id = :case_id ORDER BY transaction_id
"""
GET_CUSTOMER_CONTACT_QUERY = "SELECT customer_id, full_name, phone, email FROM customers WHERE customer_id = :customer_id"
GET_CUSTOMER_WORKPLACE_QUERY = "SELECT customer_id, employer, work_city AS city FROM customers WHERE customer_id = :customer_id"
GET_CUSTOMER_ID_QUERY = "SELECT customer_id FROM customers WHERE customer_id = :customer_id"
GET_CUSTOMER_PROFILE_QUERY = """
SELECT customer_id, segment, branch, customer_since,
       (SELECT COUNT(*) FROM transactions JOIN accounts USING (account_number)
        WHERE accounts.customer_id = customers.customer_id) AS transaction_count
FROM customers WHERE customer_id = :customer_id
"""
# Account numbers, even masked, and block status are left out: the blocked-accounts listing maps them to reasons
# such as "AML investigation hold", which would reveal an AML review without recording the fact.
LIST_CUSTOMER_ACCOUNTS_QUERY = """
SELECT account_type, opened_on FROM accounts WHERE customer_id = :customer_id ORDER BY opened_on, account_type
"""
LIST_CUSTOMER_TRANSACTIONS_QUERY = """
SELECT transaction_id AS id, booked_on AS date, amount_pln, direction, channel, counterparty,
       anomaly_pattern AS pattern
FROM transactions JOIN accounts USING (account_number)
WHERE accounts.customer_id = :customer_id
ORDER BY booked_on DESC, transaction_id LIMIT :limit
"""
# Statistics: every query takes an optional :branch. AML cases and blocked accounts count under the customer's branch.
COUNT_CUSTOMERS_BY_SEGMENT_QUERY = """
SELECT segment AS name, COUNT(*) AS count FROM customers WHERE (:branch IS NULL OR branch = :branch)
GROUP BY segment ORDER BY segment
"""
COUNT_CUSTOMERS_BY_BRANCH_QUERY = """
SELECT branch AS name, COUNT(*) AS count FROM customers WHERE (:branch IS NULL OR branch = :branch)
GROUP BY branch ORDER BY branch
"""
SUM_TRANSACTIONS_BY_MONTH_QUERY = """
SELECT SUBSTR(booked_on, 1, 7) AS month, COUNT(*) AS count, ROUND(SUM(amount_pln), 2) AS volume_pln
FROM transactions WHERE (:branch IS NULL OR branch = :branch)
GROUP BY month ORDER BY month
"""
COUNT_TRANSACTIONS_BY_CHANNEL_QUERY = """
SELECT channel AS name, COUNT(*) AS count FROM transactions WHERE (:branch IS NULL OR branch = :branch)
GROUP BY channel ORDER BY channel
"""
COUNT_ANOMALIES_BY_PATTERN_QUERY = """
SELECT anomaly_pattern AS name, COUNT(*) AS count FROM transactions
WHERE anomaly_pattern IS NOT NULL AND (:branch IS NULL OR branch = :branch)
GROUP BY anomaly_pattern ORDER BY anomaly_pattern
"""
COUNT_AML_CASES_BY_STATUS_QUERY = """
SELECT status AS name, COUNT(*) AS count FROM aml_cases JOIN customers USING (customer_id)
WHERE (:branch IS NULL OR customers.branch = :branch)
GROUP BY status ORDER BY status
"""
COUNT_AML_CASES_BY_RISK_LEVEL_QUERY = """
SELECT risk_level AS name, COUNT(*) AS count FROM aml_cases JOIN customers USING (customer_id)
WHERE (:branch IS NULL OR customers.branch = :branch)
GROUP BY risk_level ORDER BY risk_level
"""
COUNT_BLOCKED_ACCOUNTS_BY_REASON_QUERY = """
SELECT block_reason AS name, COUNT(*) AS count FROM accounts JOIN customers USING (customer_id)
WHERE blocked_on IS NOT NULL AND (:branch IS NULL OR customers.branch = :branch)
GROUP BY block_reason ORDER BY block_reason
"""


class BankDemoDatabase:
    def __init__(self, db_path: str | Path):
        self._read_only_uri = Path(db_path).resolve().as_uri() + READ_ONLY_URI_SUFFIX

    def list_customers(self, branch: str | None = None, segment: str | None = None) -> list[dict[str, Any]]:
        return self._fetch_all(LIST_CUSTOMERS_QUERY, {"branch": branch, "segment": segment, "limit": MAX_RETURNED_ROWS})

    def list_transaction_anomalies(self, branch: str | None = None, pattern: str | None = None,
                                   date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
        return self._fetch_all(LIST_TRANSACTION_ANOMALIES_QUERY, {
            "branch": branch, "pattern": pattern, "date_from": date_from, "date_to": date_to,
            "limit": MAX_RETURNED_ROWS,
        })

    def list_transactions(self, branch: str | None = None, channel: str | None = None, direction: str | None = None,
                          min_amount_pln: float | None = None, date_from: str | None = None,
                          date_to: str | None = None, order_by: str = NEWEST_FIRST) -> list[dict[str, Any]]:
        return self._fetch_all(LIST_TRANSACTIONS_QUERY, {
            "branch": branch, "channel": channel, "direction": direction, "min_amount_pln": min_amount_pln,
            "date_from": date_from, "date_to": date_to, "order_by": order_by, "largest_first": LARGEST_FIRST,
            "limit": MAX_RETURNED_ROWS,
        })

    def get_transaction_details(self, transaction_id: str) -> dict[str, Any]:
        return self._fetch_one(GET_TRANSACTION_DETAILS_QUERY, {"transaction_id": transaction_id})

    def list_blocked_accounts(self) -> list[dict[str, Any]]:
        return self._fetch_all(LIST_BLOCKED_ACCOUNTS_QUERY,
                               {"mask_prefix": MASKED_ACCOUNT_PREFIX, "limit": MAX_RETURNED_ROWS})

    def list_aml_cases(self, status: str | None = None, risk_level: str | None = None) -> list[dict[str, Any]]:
        return self._fetch_all(LIST_AML_CASES_QUERY,
                               {"status": status, "risk_level": risk_level, "limit": MAX_RETURNED_ROWS})

    def get_aml_case(self, case_id: str) -> dict[str, Any]:
        case = self._fetch_one(GET_AML_CASE_QUERY, {"case_id": case_id})
        linked_rows = self._fetch_all(LIST_AML_CASE_TRANSACTIONS_QUERY, {"case_id": case_id})
        return {**case, "linked_transactions": [row["transaction_id"] for row in linked_rows]}

    def get_customer_contact(self, customer_id: str) -> dict[str, Any]:
        return self._fetch_one(GET_CUSTOMER_CONTACT_QUERY, {"customer_id": customer_id})

    def get_customer_workplace(self, customer_id: str) -> dict[str, Any]:
        return self._fetch_one(GET_CUSTOMER_WORKPLACE_QUERY, {"customer_id": customer_id})

    def get_customer_profile(self, customer_id: str) -> dict[str, Any]:
        profile = self._fetch_one(GET_CUSTOMER_PROFILE_QUERY, {"customer_id": customer_id})
        accounts = self._fetch_all(LIST_CUSTOMER_ACCOUNTS_QUERY, {"customer_id": customer_id})
        return {**profile, "accounts": accounts}

    def list_customer_transactions(self, customer_id: str) -> list[dict[str, Any]]:
        # An unknown customer must fail like any missing record, not look like a customer without transactions.
        self._fetch_one(GET_CUSTOMER_ID_QUERY, {"customer_id": customer_id})
        return self._fetch_all(LIST_CUSTOMER_TRANSACTIONS_QUERY,
                               {"customer_id": customer_id, "limit": MAX_RETURNED_ROWS})

    def get_statistics(self, branch: str | None = None) -> dict[str, Any]:
        parameters = {"branch": branch}
        return {
            "branch": branch or ALL_BRANCHES,
            "customers_by_segment": self._count_by_name(COUNT_CUSTOMERS_BY_SEGMENT_QUERY, parameters),
            "customers_by_branch": self._count_by_name(COUNT_CUSTOMERS_BY_BRANCH_QUERY, parameters),
            "transactions_by_month": self._fetch_all(SUM_TRANSACTIONS_BY_MONTH_QUERY, parameters),
            "transactions_by_channel": self._count_by_name(COUNT_TRANSACTIONS_BY_CHANNEL_QUERY, parameters),
            "anomalies_by_pattern": self._count_by_name(COUNT_ANOMALIES_BY_PATTERN_QUERY, parameters),
            "aml_cases_by_status": self._count_by_name(COUNT_AML_CASES_BY_STATUS_QUERY, parameters),
            "aml_cases_by_risk_level": self._count_by_name(COUNT_AML_CASES_BY_RISK_LEVEL_QUERY, parameters),
            "blocked_accounts_by_reason": self._count_by_name(COUNT_BLOCKED_ACCOUNTS_BY_REASON_QUERY, parameters),
        }

    def _count_by_name(self, query: str, parameters: dict[str, Any]) -> dict[str, int]:
        return {row["name"]: row["count"] for row in self._fetch_all(query, parameters)}

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
