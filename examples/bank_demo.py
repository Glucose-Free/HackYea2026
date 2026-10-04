"""Example domain: the gateway demo's five data tools.

The records live in `bank_demo_seed.sql` and are seeded into a SQLite database; the executor only
queries them read-only, so no demo record is hard-coded in Python.

Facts are keyed by customer ID, so the AML case and the customer's contact or workplace meet on one
subject. Planning cannot know which customer an AML case names before reading it, so the summary is
planned for any customer ID: a user who already knows some customer's contact is refused every
AML summary. That is the core's conservative approximation; it over-blocks rather than consult
private data before deciding.
"""
from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any, Callable, Iterable
import os
import re
import sqlite3

from policy_middleware import (
    DatalogAtom, DatalogRule, KnowledgeFact, PlannedFact, PolicyError, PolicyRuleConfig,
    ToolDefinition, ToolRegistry, ValueDomain, require_fields, validate_text,
)

ANALYST_ROLE = "data_analyst"
RULES_VERSION = "bank-demo-v1"

DEFAULT_BANK_DEMO_DB_PATH = os.environ.get("POLICY_DEMO_DB_PATH", "bank_demo.sqlite3")
BANK_DEMO_SEED_PATH = str(Path(__file__).with_name("bank_demo_seed.sql"))

TRANSACTION_ANOMALIES_TOOL = "get_transaction_anomalies"
BLOCKED_ACCOUNTS_TOOL = "get_blocked_accounts"
AML_CASE_SUMMARY_TOOL = "get_aml_case_summary"
CUSTOMER_CONTACT_TOOL = "get_customer_contact"
CUSTOMER_WORKPLACE_TOOL = "get_customer_workplace"

CUSTOMER_ID_FIELD = "customer_id"
CUSTOMER_ID_PATTERN = r"CUST-[0-9]{1,12}"
AML_REVIEW_RELATION = "aml_review"
CONTACT_RELATION = "contact_data"
WORKPLACE_RELATION = "workplace_data"
DISCLOSED_VALUE = "disclosed"
CONTACT_RULE_ID = "aml_contact"
WORKPLACE_RULE_ID = "aml_workplace"
MAX_LISTED_ROWS = 100

ANOMALY_FIELDS = {"id", "date", "amount_pln", "branch", "pattern"}
BLOCKED_ACCOUNT_FIELDS = {"account", "blocked_on", "reason"}
AML_CASE_FIELDS = {"case_id", "status", "customer_id", "linked_transactions"}
CONTACT_FIELDS = {"customer_id", "phone", "email"}
WORKPLACE_FIELDS = {"customer_id", "employer", "city"}


def knows(*terms: str) -> DatalogAtom:
    return DatalogAtom("knows", terms)


def build_aml_combination_rule(rule_id: str, customer_relation: str) -> PolicyRuleConfig:
    return PolicyRuleConfig(rule_id, DatalogRule(
        rule_id,
        DatalogAtom("violation", ("?u", "?customer", rule_id)),
        (knows("?u", "?customer", AML_REVIEW_RELATION, "?status"),
         knows("?u", "?customer", customer_relation, "?value")),
    ))


def default_rules() -> list[PolicyRuleConfig]:
    return [
        build_aml_combination_rule(CONTACT_RULE_ID, CONTACT_RELATION),
        build_aml_combination_rule(WORKPLACE_RULE_ID, WORKPLACE_RELATION),
    ]


def parse_no_arguments(arguments: dict) -> dict:
    require_fields(arguments, set())
    return {}


def parse_customer_arguments(arguments: dict) -> dict:
    require_fields(arguments, {CUSTOMER_ID_FIELD})
    return {CUSTOMER_ID_FIELD: parse_customer_id(arguments[CUSTOMER_ID_FIELD])}


def parse_customer_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(CUSTOMER_ID_PATTERN, value):
        raise PolicyError("Invalid customer ID")
    return value


def plan_nothing(args: dict, history: Iterable[KnowledgeFact]) -> list[PlannedFact]:
    return []  # Anonymized rows: masked accounts and transactions name no customer.


def plan_aml_case(args: dict, history: Iterable[KnowledgeFact]) -> list[PlannedFact]:
    return [PlannedFact(ValueDomain(pattern=CUSTOMER_ID_PATTERN), AML_REVIEW_RELATION, ValueDomain())]


def plan_customer_disclosure(relation: str, args: dict, history: Iterable[KnowledgeFact]) -> list[KnowledgeFact]:
    return [KnowledgeFact(args[CUSTOMER_ID_FIELD], relation, DISCLOSED_VALUE)]


def validate_rows(required_fields: set[str], args: dict, rows: Any) -> tuple[list, list[KnowledgeFact]]:
    if not isinstance(rows, list) or len(rows) > MAX_LISTED_ROWS:
        raise PolicyError("Invalid row list")
    for row in rows:
        require_fields(row, required_fields)
    return rows, []


def validate_aml_case(args: dict, case: Any) -> tuple[dict, list[KnowledgeFact]]:
    require_fields(case, AML_CASE_FIELDS)
    customer_id = parse_customer_id(case[CUSTOMER_ID_FIELD])
    status = validate_text(case["status"], "AML case status", 256)
    return case, [KnowledgeFact(customer_id, AML_REVIEW_RELATION, status)]


def validate_customer_disclosure(required_fields: set[str], relation: str, args: dict,
                                 record: Any) -> tuple[dict, list[KnowledgeFact]]:
    require_fields(record, required_fields)
    if record[CUSTOMER_ID_FIELD] != args[CUSTOMER_ID_FIELD]:
        raise PolicyError("Response refers to a different customer")
    for name in required_fields - {CUSTOMER_ID_FIELD}:
        validate_text(record[name], name, 1000)
    return record, [KnowledgeFact(args[CUSTOMER_ID_FIELD], relation, DISCLOSED_VALUE)]


NO_ARGUMENTS_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {}, "required": []}
CUSTOMER_ARGUMENTS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {CUSTOMER_ID_FIELD: {"type": "string", "pattern": "^" + CUSTOMER_ID_PATTERN + "$"}},
    "required": [CUSTOMER_ID_FIELD],
}


def build_tool(name: str, description: str, input_schema: dict, validate_arguments: Callable,
               plan_disclosure: Callable, validate_response: Callable) -> ToolDefinition:
    return ToolDefinition(
        name=name, description=description, input_schema=input_schema,
        allowed_roles=frozenset({ANALYST_ROLE}), validate_arguments=validate_arguments,
        plan_disclosure=plan_disclosure, validate_response=validate_response,
    )


def tool_definitions() -> tuple[ToolDefinition, ...]:
    return (
        build_tool(TRANSACTION_ANOMALIES_TOOL,
                   "List recent anomalous transactions (id, date, amount, branch, detected pattern). Anonymized.",
                   NO_ARGUMENTS_SCHEMA, parse_no_arguments, plan_nothing, partial(validate_rows, ANOMALY_FIELDS)),
        build_tool(BLOCKED_ACCOUNTS_TOOL,
                   "List recently blocked accounts with masked numbers and the blocking reason. Anonymized.",
                   NO_ARGUMENTS_SCHEMA, parse_no_arguments, plan_nothing,
                   partial(validate_rows, BLOCKED_ACCOUNT_FIELDS)),
        build_tool(AML_CASE_SUMMARY_TOOL,
                   "Get the summary of the current AML (anti-money-laundering) case, including the customer id.",
                   NO_ARGUMENTS_SCHEMA, parse_no_arguments, plan_aml_case, validate_aml_case),
        build_tool(CUSTOMER_CONTACT_TOOL, "Get a customer's contact details (phone, email) by customer id.",
                   CUSTOMER_ARGUMENTS_SCHEMA, parse_customer_arguments,
                   partial(plan_customer_disclosure, CONTACT_RELATION),
                   partial(validate_customer_disclosure, CONTACT_FIELDS, CONTACT_RELATION)),
        build_tool(CUSTOMER_WORKPLACE_TOOL, "Get a customer's employer and work location by customer id.",
                   CUSTOMER_ARGUMENTS_SCHEMA, parse_customer_arguments,
                   partial(plan_customer_disclosure, WORKPLACE_RELATION),
                   partial(validate_customer_disclosure, WORKPLACE_FIELDS, WORKPLACE_RELATION)),
    )


def build_registry() -> ToolRegistry:
    return ToolRegistry(tool_definitions())


class BankDemoStore:
    """Read-only access to the fictional bank records seeded from `bank_demo_seed.sql`."""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    def _query_all(self, sql: str, parameters: tuple = ()) -> list[dict]:
        connection = self._connect()
        try:
            return [dict(row) for row in connection.execute(sql, parameters).fetchall()]
        finally:
            connection.close()

    def _query_one(self, sql: str, parameters: tuple = ()) -> dict | None:
        connection = self._connect()
        try:
            row = connection.execute(sql, parameters).fetchone()
            return dict(row) if row is not None else None
        finally:
            connection.close()

    def transaction_anomalies(self) -> list[dict]:
        return self._query_all("SELECT * FROM transaction_anomalies ORDER BY id")

    def blocked_accounts(self) -> list[dict]:
        return self._query_all("SELECT * FROM blocked_accounts ORDER BY blocked_on, account")

    def aml_case_summary(self) -> dict:
        case = self._query_one("SELECT case_id, status, customer_id FROM aml_cases ORDER BY case_id LIMIT 1")
        if case is None:
            raise LookupError("No AML case")
        case["linked_transactions"] = [row["transaction_id"] for row in self._query_all(
            "SELECT transaction_id FROM aml_case_transactions WHERE case_id = ? ORDER BY position",
            (case["case_id"],))]
        return case

    def customer_contact(self, customer_id: str) -> dict:
        record = self._query_one("SELECT phone, email FROM customer_contacts WHERE customer_id = ?", (customer_id,))
        if record is None:
            raise LookupError("No such record")
        return {CUSTOMER_ID_FIELD: customer_id, **record}

    def customer_workplace(self, customer_id: str) -> dict:
        record = self._query_one("SELECT employer, city FROM customer_workplaces WHERE customer_id = ?", (customer_id,))
        if record is None:
            raise LookupError("No such record")
        return {CUSTOMER_ID_FIELD: customer_id, **record}


def initialize_bank_demo_db(db_path: str = DEFAULT_BANK_DEMO_DB_PATH,
                            seed_path: str = BANK_DEMO_SEED_PATH) -> BankDemoStore:
    """Create and seed the demo database; idempotent, so it is safe on every startup."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30)
    try:
        connection.executescript(Path(seed_path).read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()
    return BankDemoStore(db_path)


def build_executor(db_path: str) -> Callable[[str, dict], Any]:
    """Trusted read-only executor over the seeded demo database; stands in for the bank's SQL."""
    store = BankDemoStore(db_path)

    def execute(tool: str, args: dict) -> Any:
        if tool == TRANSACTION_ANOMALIES_TOOL:
            return store.transaction_anomalies()
        if tool == BLOCKED_ACCOUNTS_TOOL:
            return store.blocked_accounts()
        if tool == AML_CASE_SUMMARY_TOOL:
            return store.aml_case_summary()
        customer_id = args.get(CUSTOMER_ID_FIELD)
        if tool == CUSTOMER_CONTACT_TOOL:
            return store.customer_contact(customer_id)
        if tool == CUSTOMER_WORKPLACE_TOOL:
            return store.customer_workplace(customer_id)
        raise LookupError("No such record")

    return execute
