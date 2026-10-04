"""Example domain: the gateway demo's five data tools. Not imported by the generic core or MCP.

Facts are keyed by customer ID, so the AML case and the customer's contact or workplace meet on one
subject. Planning cannot know which customer an AML case names before reading it, so the summary is
planned for any customer ID: a user who already knows some customer's contact is refused every
AML summary. That is the core's conservative approximation; it over-blocks rather than consult
private data before deciding.
"""
from __future__ import annotations

from functools import partial
from typing import Any, Callable, Iterable
import re

from policy_middleware import (
    DatalogAtom, DatalogRule, KnowledgeFact, PlannedFact, PolicyError, PolicyRuleConfig,
    ToolDefinition, ToolRegistry, ValueDomain, require_fields, validate_text,
)

ANALYST_ROLE = "data_analyst"
RULES_VERSION = "bank-demo-v1"

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

TRANSACTION_ANOMALIES = [
    {"id": "TX-1001", "date": "2026-09-28", "amount_pln": 12500.00, "branch": "Warsaw", "pattern": "structuring"},
    {"id": "TX-1003", "date": "2026-09-30", "amount_pln": 47000.00, "branch": "Warsaw", "pattern": "rapid in-out"},
]
BLOCKED_ACCOUNTS = [
    {"account": "PL-****-4411", "blocked_on": "2026-09-30", "reason": "suspicious inflows"},
    {"account": "PL-****-9032", "blocked_on": "2026-10-01", "reason": "court order"},
]
AML_CASE_SUMMARY = {
    "case_id": "AML-2026-0042",
    "status": "under investigation",
    "customer_id": "CUST-17",
    "linked_transactions": ["TX-1001", "TX-1003"],
}
CUSTOMER_CONTACTS = {"CUST-17": {"phone": "+48 601 234 567", "email": "j.kowalski@example.pl"}}
CUSTOMER_WORKPLACES = {"CUST-17": {"employer": "Kowalski Logistics sp. z o.o.", "city": "Warsaw"}}

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


def demo_executor(tool: str, args: dict) -> Any:
    """Fictional fixture data standing in for the bank's read-only SQL."""
    if tool == TRANSACTION_ANOMALIES_TOOL:
        return TRANSACTION_ANOMALIES
    if tool == BLOCKED_ACCOUNTS_TOOL:
        return BLOCKED_ACCOUNTS
    if tool == AML_CASE_SUMMARY_TOOL:
        return AML_CASE_SUMMARY
    customer_id = args.get(CUSTOMER_ID_FIELD)
    if tool == CUSTOMER_CONTACT_TOOL and customer_id in CUSTOMER_CONTACTS:
        return {CUSTOMER_ID_FIELD: customer_id, **CUSTOMER_CONTACTS[customer_id]}
    if tool == CUSTOMER_WORKPLACE_TOOL and customer_id in CUSTOMER_WORKPLACES:
        return {CUSTOMER_ID_FIELD: customer_id, **CUSTOMER_WORKPLACES[customer_id]}
    raise LookupError("No such record")
