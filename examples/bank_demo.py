"""Example domain: the gateway demo's bank data tools. Not imported by the generic core or MCP.

The data comes from the SQLite database built by `examples.bank_demo_seed` and read by `examples.bank_demo_db`.
The listing tools are anonymized (no customer ID next to anything identifying, account numbers masked), so they
disclose no facts; only the AML case summary and the two customer lookups do.

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
import re

from examples.bank_demo_db import BRANCHES, FEATURED_AML_CASE_ID, BankDemoDatabase

from policy_middleware import (
    DatalogAtom, DatalogRule, KnowledgeFact, PlannedFact, PolicyError, PolicyRuleConfig,
    ToolDefinition, ToolRegistry, ValueDomain, require_fields, validate_text,
)

ANALYST_ROLE = "data_analyst"
RULES_VERSION = "bank-demo-v1"

CUSTOMERS_TOOL = "list_customers"
TRANSACTION_ANOMALIES_TOOL = "get_transaction_anomalies"
BLOCKED_ACCOUNTS_TOOL = "get_blocked_accounts"
AML_CASES_TOOL = "list_aml_cases"
AML_CASE_SUMMARY_TOOL = "get_aml_case_summary"
CUSTOMER_CONTACT_TOOL = "get_customer_contact"
CUSTOMER_WORKPLACE_TOOL = "get_customer_workplace"

CUSTOMER_ID_FIELD = "customer_id"
CUSTOMER_ID_PATTERN = r"CUST-[0-9]{1,12}"
BRANCH_FIELD = "branch"
CASE_ID_FIELD = "case_id"
CASE_ID_PATTERN = r"AML-[0-9]{4}-[0-9]{4}"
AML_REVIEW_RELATION = "aml_review"
CONTACT_RELATION = "contact_data"
WORKPLACE_RELATION = "workplace_data"
DISCLOSED_VALUE = "disclosed"
CONTACT_RULE_ID = "aml_contact"
WORKPLACE_RULE_ID = "aml_workplace"
MAX_LISTED_ROWS = 100

CUSTOMER_LISTING_FIELDS = {"customer_id", "segment", "branch", "customer_since"}
ANOMALY_FIELDS = {"id", "date", "amount_pln", "branch", "pattern"}
BLOCKED_ACCOUNT_FIELDS = {"account", "blocked_on", "reason"}
AML_CASE_LISTING_FIELDS = {"case_id", "status", "risk_level", "opened_on", "linked_transaction_count"}
AML_CASE_FIELDS = {"case_id", "status", "risk_level", "opened_on", "customer_id", "linked_transactions"}
CONTACT_FIELDS = {"customer_id", "full_name", "phone", "email"}
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


def parse_branch_filter_arguments(arguments: dict) -> dict:
    require_fields(arguments, set(), {BRANCH_FIELD})
    if BRANCH_FIELD in arguments and arguments[BRANCH_FIELD] not in BRANCHES:
        raise PolicyError("Unknown branch")
    return dict(arguments)


def parse_aml_case_arguments(arguments: dict) -> dict:
    require_fields(arguments, set(), {CASE_ID_FIELD})
    case_id = arguments.get(CASE_ID_FIELD, FEATURED_AML_CASE_ID)
    if not isinstance(case_id, str) or not re.fullmatch(CASE_ID_PATTERN, case_id):
        raise PolicyError("Invalid AML case ID")
    return {CASE_ID_FIELD: case_id}


def parse_customer_arguments(arguments: dict) -> dict:
    require_fields(arguments, {CUSTOMER_ID_FIELD})
    return {CUSTOMER_ID_FIELD: parse_customer_id(arguments[CUSTOMER_ID_FIELD])}


def parse_customer_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(CUSTOMER_ID_PATTERN, value):
        raise PolicyError("Invalid customer ID")
    return value


def plan_nothing(args: dict, history: Iterable[KnowledgeFact]) -> list[PlannedFact]:
    return []  # Anonymized rows: no row puts a customer ID next to anything identifying.


def plan_aml_case(args: dict, history: Iterable[KnowledgeFact]) -> list[PlannedFact]:
    # The case ID does not tell which customer the case names until the row is read, so plan for any customer.
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
    if case[CASE_ID_FIELD] != args[CASE_ID_FIELD]:
        raise PolicyError("Response refers to a different AML case")
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
BRANCH_FILTER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {BRANCH_FIELD: {"type": "string", "enum": list(BRANCHES), "description": "Only this branch."}},
    "required": [],
}
AML_CASE_ARGUMENTS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {CASE_ID_FIELD: {"type": "string", "pattern": "^" + CASE_ID_PATTERN + "$",
                                   "description": f"Defaults to the featured case, {FEATURED_AML_CASE_ID}."}},
    "required": [],
}
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
    # Listing tools come before the lookups whose names overlap theirs: the stub chat model breaks ties by order.
    return (
        build_tool(CUSTOMERS_TOOL,
                   "List customers (id, segment, home branch, customer since), optionally for one branch. "
                   "Anonymized: no names or contact details. Returns at most 50 rows.",
                   BRANCH_FILTER_SCHEMA, parse_branch_filter_arguments, plan_nothing,
                   partial(validate_rows, CUSTOMER_LISTING_FIELDS)),
        build_tool(TRANSACTION_ANOMALIES_TOOL,
                   "List the newest anomalous transactions (id, date, amount, branch, detected pattern), "
                   "optionally for one branch. Anonymized. Returns at most 50 rows.",
                   BRANCH_FILTER_SCHEMA, parse_branch_filter_arguments, plan_nothing,
                   partial(validate_rows, ANOMALY_FIELDS)),
        build_tool(BLOCKED_ACCOUNTS_TOOL,
                   "List the most recently blocked accounts with masked numbers and the blocking reason. Anonymized.",
                   NO_ARGUMENTS_SCHEMA, parse_no_arguments, plan_nothing,
                   partial(validate_rows, BLOCKED_ACCOUNT_FIELDS)),
        build_tool(AML_CASES_TOOL,
                   "List AML (anti-money-laundering) cases (case id, status, risk level, opening date, number of "
                   "linked transactions), newest first. Anonymized: does not name the customers.",
                   NO_ARGUMENTS_SCHEMA, parse_no_arguments, plan_nothing,
                   partial(validate_rows, AML_CASE_LISTING_FIELDS)),
        build_tool(AML_CASE_SUMMARY_TOOL,
                   "Get the summary of one AML case, including the customer id and linked transactions. "
                   f"Without a case id, returns the featured case {FEATURED_AML_CASE_ID}.",
                   AML_CASE_ARGUMENTS_SCHEMA, parse_aml_case_arguments, plan_aml_case, validate_aml_case),
        build_tool(CUSTOMER_CONTACT_TOOL, "Get a customer's name and contact details (phone, email) by customer id.",
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


def build_executor(db_path: str | Path) -> Callable[[str, dict], Any]:
    """The middleware's executor: runs a validated tool call as a read-only query on the demo database."""
    database = BankDemoDatabase(db_path)
    queries: dict[str, Callable[[dict], Any]] = {
        CUSTOMERS_TOOL: lambda args: database.list_customers(args.get(BRANCH_FIELD)),
        TRANSACTION_ANOMALIES_TOOL: lambda args: database.list_transaction_anomalies(args.get(BRANCH_FIELD)),
        BLOCKED_ACCOUNTS_TOOL: lambda args: database.list_blocked_accounts(),
        AML_CASES_TOOL: lambda args: database.list_aml_cases(),
        AML_CASE_SUMMARY_TOOL: lambda args: database.get_aml_case(args[CASE_ID_FIELD]),
        CUSTOMER_CONTACT_TOOL: lambda args: database.get_customer_contact(args[CUSTOMER_ID_FIELD]),
        CUSTOMER_WORKPLACE_TOOL: lambda args: database.get_customer_workplace(args[CUSTOMER_ID_FIELD]),
    }

    def execute(tool: str, args: dict) -> Any:
        return queries[tool](args)

    return execute
