"""Example domain: the gateway demo's bank data tools. Not imported by the generic core or MCP.

The data comes from the SQLite database built by `examples.bank_demo_seed` and read by `examples.bank_demo_db`.
The listing, statistics and transaction tools are anonymized (no customer ID next to anything identifying or next to
a transaction, account numbers masked), so they disclose no facts. The AML case summary, the contact and workplace
lookups and a customer's transaction history do.

Facts are keyed by customer ID, so the AML case and the customer's contact or workplace meet on one
subject. Planning cannot know which customer an AML case names before reading it, so the summary is
planned for any customer ID: a user who already knows some customer's contact is refused every
AML summary. That is the core's conservative approximation; it over-blocks rather than consult
private data before deciding.
"""
from __future__ import annotations

from datetime import date
from functools import partial
from pathlib import Path
from typing import Any, Callable, Iterable
import math
import re

from examples.bank_demo_db import (
    BRANCHES, FEATURED_AML_CASE_ID, MAX_RETURNED_ROWS, TRANSACTION_ORDERS, BankDemoDatabase,
)
from examples.bank_demo_seed import (
    ANOMALY_PATTERNS, AML_STATUSES, AS_OF_DATE, CHANNELS, DIRECTIONS, HISTORY_START_DATE, RISK_LEVELS, SEGMENTS,
)

from policy_engine.middleware import (
    DatalogAtom, DatalogRule, KnowledgeFact, PlannedFact, PolicyError, PolicyRuleConfig,
    ToolDefinition, ToolRegistry, ValueDomain, require_fields, validate_text,
)

ANALYST_ROLE = "data_analyst"
RULES_VERSION = "bank-demo-v2"

CUSTOMERS_TOOL = "list_customers"
TRANSACTIONS_TOOL = "list_transactions"
TRANSACTION_ANOMALIES_TOOL = "get_transaction_anomalies"
BLOCKED_ACCOUNTS_TOOL = "get_blocked_accounts"
AML_CASES_TOOL = "list_aml_cases"
AML_CASE_SUMMARY_TOOL = "get_aml_case_summary"
CUSTOMER_CONTACT_TOOL = "get_customer_contact"
CUSTOMER_WORKPLACE_TOOL = "get_customer_workplace"
CUSTOMER_PROFILE_TOOL = "get_customer_profile"
CUSTOMER_TRANSACTIONS_TOOL = "list_customer_transactions"
TRANSACTION_DETAILS_TOOL = "get_transaction_details"
STATISTICS_TOOL = "get_statistics"

CUSTOMER_ID_FIELD = "customer_id"
CUSTOMER_ID_PATTERN = r"CUST-[0-9]{1,12}"
TRANSACTION_ID_FIELD = "transaction_id"
TRANSACTION_ID_PATTERN = r"TX-[0-9]{1,12}"
CASE_ID_FIELD = "case_id"
CASE_ID_PATTERN = r"AML-[0-9]{4}-[0-9]{4}"
BRANCH_FIELD = "branch"
SEGMENT_FIELD = "segment"
PATTERN_FIELD = "pattern"
CHANNEL_FIELD = "channel"
DIRECTION_FIELD = "direction"
MIN_AMOUNT_FIELD = "min_amount_pln"
DATE_FROM_FIELD = "date_from"
DATE_TO_FIELD = "date_to"
ORDER_BY_FIELD = "order_by"
STATUS_FIELD = "status"
RISK_LEVEL_FIELD = "risk_level"
ISO_DATE_PATTERN = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
MAX_AMOUNT_PLN = 1_000_000_000

AML_REVIEW_RELATION = "aml_review"
CONTACT_RELATION = "contact_data"
WORKPLACE_RELATION = "workplace_data"
TRANSACTION_HISTORY_RELATION = "transaction_history"
DISCLOSED_VALUE = "disclosed"
CONTACT_RULE_ID = "aml_contact"
WORKPLACE_RULE_ID = "aml_workplace"
HISTORY_CONTACT_RULE_ID = "history_contact"
HISTORY_WORKPLACE_RULE_ID = "history_workplace"

CUSTOMER_LISTING_FIELDS = {"customer_id", "segment", "branch", "customer_since"}
TRANSACTION_FIELDS = {"id", "date", "amount_pln", "direction", "channel", "counterparty", "branch", "pattern"}
ANOMALY_FIELDS = {"id", "date", "amount_pln", "branch", "pattern"}
BLOCKED_ACCOUNT_FIELDS = {"account", "blocked_on", "reason"}
AML_CASE_LISTING_FIELDS = {"case_id", "status", "risk_level", "opened_on", "linked_transaction_count"}
AML_CASE_FIELDS = {"case_id", "status", "risk_level", "opened_on", "customer_id", "linked_transactions"}
CONTACT_FIELDS = {"customer_id", "full_name", "phone", "email"}
WORKPLACE_FIELDS = {"customer_id", "employer", "city"}
CUSTOMER_PROFILE_FIELDS = {"customer_id", "segment", "branch", "customer_since", "transaction_count", "accounts"}
CUSTOMER_TRANSACTION_FIELDS = {"id", "date", "amount_pln", "direction", "channel", "counterparty", "pattern"}
STATISTICS_FIELDS = {"branch", "customers_by_segment", "customers_by_branch", "transactions_by_month",
                     "transactions_by_channel", "anomalies_by_pattern", "aml_cases_by_status",
                     "aml_cases_by_risk_level", "blocked_accounts_by_reason"}

FieldParser = Callable[[Any], Any]


def knows(*terms: str) -> DatalogAtom:
    return DatalogAtom("knows", terms)


def build_combination_rule(rule_id: str, first_relation: str, second_relation: str) -> PolicyRuleConfig:
    """Forbids one user from knowing both relations about the same customer, whichever was learned first."""
    return PolicyRuleConfig(rule_id, DatalogRule(
        rule_id,
        DatalogAtom("violation", ("?u", "?customer", rule_id)),
        (knows("?u", "?customer", first_relation, "?first"),
         knows("?u", "?customer", second_relation, "?second")),
    ))


def default_rules() -> list[PolicyRuleConfig]:
    return [
        build_combination_rule(CONTACT_RULE_ID, AML_REVIEW_RELATION, CONTACT_RELATION),
        build_combination_rule(WORKPLACE_RULE_ID, AML_REVIEW_RELATION, WORKPLACE_RELATION),
        # Behavioral analysis stays pseudonymous: a named person's full transaction history is profiling, and its
        # anomaly patterns hint at an AML review without the summary that would record one.
        build_combination_rule(HISTORY_CONTACT_RULE_ID, TRANSACTION_HISTORY_RELATION, CONTACT_RELATION),
        build_combination_rule(HISTORY_WORKPLACE_RULE_ID, TRANSACTION_HISTORY_RELATION, WORKPLACE_RELATION),
    ]


def parse_arguments(field_parsers: dict[str, FieldParser], required: set[str], arguments: dict) -> dict:
    given_arguments = drop_null_arguments(arguments)
    require_fields(given_arguments, required, set(field_parsers) - required)
    parsed = {name: field_parsers[name](value) for name, value in given_arguments.items()}
    require_ordered_date_range(parsed)
    return parsed


def drop_null_arguments(arguments: Any) -> Any:
    # Small chat models send null for an optional filter they mean to leave out; refusing it would fail the fetch.
    if not isinstance(arguments, dict):
        return arguments
    return {name: value for name, value in arguments.items() if value is not None}


def require_ordered_date_range(arguments: dict) -> None:
    if DATE_FROM_FIELD in arguments and DATE_TO_FIELD in arguments \
            and arguments[DATE_FROM_FIELD] > arguments[DATE_TO_FIELD]:
        raise PolicyError("date_from is after date_to")


def parse_choice(choices: tuple[str, ...], name: str, value: Any) -> str:
    if value not in choices:
        raise PolicyError(f"Unknown {name}")
    return value


def parse_iso_date(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(ISO_DATE_PATTERN, value):
        raise PolicyError("Invalid date")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise PolicyError("Invalid date") from error
    return value


def parse_amount(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or not 0 <= value <= MAX_AMOUNT_PLN:
        raise PolicyError("Invalid amount")
    return value


def parse_identifier(pattern: str, name: str, value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise PolicyError(f"Invalid {name}")
    return value


parse_customer_id = partial(parse_identifier, CUSTOMER_ID_PATTERN, "customer ID")
parse_transaction_id = partial(parse_identifier, TRANSACTION_ID_PATTERN, "transaction ID")
parse_branch = partial(parse_choice, BRANCHES, "branch")

CUSTOMER_FILTER_PARSERS = {BRANCH_FIELD: parse_branch, SEGMENT_FIELD: partial(parse_choice, SEGMENTS, "segment")}
DATE_RANGE_PARSERS = {DATE_FROM_FIELD: parse_iso_date, DATE_TO_FIELD: parse_iso_date}
TRANSACTION_FILTER_PARSERS = {
    BRANCH_FIELD: parse_branch,
    CHANNEL_FIELD: partial(parse_choice, CHANNELS, "channel"),
    DIRECTION_FIELD: partial(parse_choice, DIRECTIONS, "direction"),
    MIN_AMOUNT_FIELD: parse_amount,
    ORDER_BY_FIELD: partial(parse_choice, TRANSACTION_ORDERS, "order"),
    **DATE_RANGE_PARSERS,
}
ANOMALY_FILTER_PARSERS = {
    BRANCH_FIELD: parse_branch, PATTERN_FIELD: partial(parse_choice, ANOMALY_PATTERNS, "anomaly pattern"),
    **DATE_RANGE_PARSERS,
}
AML_CASE_FILTER_PARSERS = {
    STATUS_FIELD: partial(parse_choice, AML_STATUSES, "AML case status"),
    RISK_LEVEL_FIELD: partial(parse_choice, RISK_LEVELS, "risk level"),
}

parse_no_arguments = partial(parse_arguments, {}, set())
parse_customer_filter_arguments = partial(parse_arguments, CUSTOMER_FILTER_PARSERS, set())
parse_transaction_filter_arguments = partial(parse_arguments, TRANSACTION_FILTER_PARSERS, set())
parse_anomaly_filter_arguments = partial(parse_arguments, ANOMALY_FILTER_PARSERS, set())
parse_aml_case_filter_arguments = partial(parse_arguments, AML_CASE_FILTER_PARSERS, set())
parse_branch_filter_arguments = partial(parse_arguments, {BRANCH_FIELD: parse_branch}, set())
parse_customer_arguments = partial(parse_arguments, {CUSTOMER_ID_FIELD: parse_customer_id}, {CUSTOMER_ID_FIELD})
parse_transaction_arguments = partial(parse_arguments, {TRANSACTION_ID_FIELD: parse_transaction_id},
                                      {TRANSACTION_ID_FIELD})


def parse_aml_case_arguments(arguments: dict) -> dict:
    given_arguments = drop_null_arguments(arguments)
    require_fields(given_arguments, set(), {CASE_ID_FIELD})
    case_id = given_arguments.get(CASE_ID_FIELD, FEATURED_AML_CASE_ID)
    return {CASE_ID_FIELD: parse_identifier(CASE_ID_PATTERN, "AML case ID", case_id)}


def plan_nothing(args: dict, history: Iterable[KnowledgeFact]) -> list[PlannedFact]:
    return []  # Anonymized: nothing puts a customer ID next to anything identifying, AML-related or transactional.


def plan_aml_case(args: dict, history: Iterable[KnowledgeFact]) -> list[PlannedFact]:
    # The case ID does not tell which customer the case names until the row is read, so plan for any customer.
    return [PlannedFact(ValueDomain(pattern=CUSTOMER_ID_PATTERN), AML_REVIEW_RELATION, ValueDomain())]


def plan_customer_disclosure(relation: str, args: dict, history: Iterable[KnowledgeFact]) -> list[KnowledgeFact]:
    return [KnowledgeFact(args[CUSTOMER_ID_FIELD], relation, DISCLOSED_VALUE)]


def validate_rows(required_fields: set[str], args: dict, rows: Any) -> tuple[list, list[KnowledgeFact]]:
    require_row_list(required_fields, rows)
    return rows, []


def require_row_list(required_fields: set[str], rows: Any) -> None:
    if not isinstance(rows, list) or len(rows) > MAX_RETURNED_ROWS:
        raise PolicyError("Invalid row list")
    for row in rows:
        require_fields(row, required_fields)


def validate_record(required_fields: set[str], id_field: str, argument_field: str, args: dict,
                    record: Any) -> tuple[dict, list[KnowledgeFact]]:
    require_fields(record, required_fields)
    if record[id_field] != args[argument_field]:
        raise PolicyError("Response refers to a different record")
    return record, []


def validate_statistics(args: dict, statistics: Any) -> tuple[dict, list[KnowledgeFact]]:
    require_fields(statistics, STATISTICS_FIELDS)
    return statistics, []


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


def validate_customer_transactions(args: dict, rows: Any) -> tuple[list, list[KnowledgeFact]]:
    require_row_list(CUSTOMER_TRANSACTION_FIELDS, rows)
    # Recorded even for an empty list: the user still learned what this customer's history is.
    return rows, [KnowledgeFact(args[CUSTOMER_ID_FIELD], TRANSACTION_HISTORY_RELATION, DISCLOSED_VALUE)]


def build_object_schema(properties: dict[str, dict], required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(required)}


def build_choice_schema(choices: tuple[str, ...], description: str) -> dict:
    return {"type": "string", "enum": list(choices), "description": description}


def build_identifier_schema(pattern: str, description: str) -> dict:
    return {"type": "string", "pattern": "^" + pattern + "$", "description": description}


BRANCH_SCHEMA = build_choice_schema(BRANCHES, "Only this branch. Leave out unless the user names a branch.")
# A chat model does not know which year the data is from; without the range it guesses one and finds nothing.
DATA_DATE_RANGE_NOTE = f"The data covers {HISTORY_START_DATE.isoformat()} to {AS_OF_DATE.isoformat()}."
DATE_RANGE_SCHEMA = {
    DATE_FROM_FIELD: {"type": "string", "format": "date", "pattern": "^" + ISO_DATE_PATTERN + "$",
                      "description": f"Earliest booking date, YYYY-MM-DD, inclusive. {DATA_DATE_RANGE_NOTE}"},
    DATE_TO_FIELD: {"type": "string", "format": "date", "pattern": "^" + ISO_DATE_PATTERN + "$",
                    "description": f"Latest booking date, YYYY-MM-DD, inclusive. {DATA_DATE_RANGE_NOTE}"},
}
NO_ARGUMENTS_SCHEMA = build_object_schema({})
BRANCH_FILTER_SCHEMA = build_object_schema({BRANCH_FIELD: BRANCH_SCHEMA})
CUSTOMER_FILTER_SCHEMA = build_object_schema({
    BRANCH_FIELD: BRANCH_SCHEMA, SEGMENT_FIELD: build_choice_schema(SEGMENTS, "Only this customer segment."),
})
TRANSACTION_FILTER_SCHEMA = build_object_schema({
    BRANCH_FIELD: BRANCH_SCHEMA,
    CHANNEL_FIELD: build_choice_schema(CHANNELS, "Only this payment channel."),
    DIRECTION_FIELD: build_choice_schema(DIRECTIONS, "'in' for incoming money, 'out' for outgoing."),
    MIN_AMOUNT_FIELD: {"type": "number", "minimum": 0, "maximum": MAX_AMOUNT_PLN,
                       "description": "Only transactions of at least this amount in PLN."},
    ORDER_BY_FIELD: build_choice_schema(TRANSACTION_ORDERS, "'newest' (default) or 'largest' amount first."),
    **DATE_RANGE_SCHEMA,
})
ANOMALY_FILTER_SCHEMA = build_object_schema({
    BRANCH_FIELD: BRANCH_SCHEMA,
    PATTERN_FIELD: build_choice_schema(ANOMALY_PATTERNS, "Only this detected pattern."),
    **DATE_RANGE_SCHEMA,
})
AML_CASE_FILTER_SCHEMA = build_object_schema({
    STATUS_FIELD: build_choice_schema(AML_STATUSES, "Only cases with this status."),
    RISK_LEVEL_FIELD: build_choice_schema(RISK_LEVELS, "Only cases with this risk level."),
})
AML_CASE_ARGUMENTS_SCHEMA = build_object_schema({
    CASE_ID_FIELD: build_identifier_schema(CASE_ID_PATTERN,
                                           f"Defaults to the featured case, {FEATURED_AML_CASE_ID}."),
})
CUSTOMER_ARGUMENTS_SCHEMA = build_object_schema(
    {CUSTOMER_ID_FIELD: build_identifier_schema(CUSTOMER_ID_PATTERN, "For example CUST-17.")}, (CUSTOMER_ID_FIELD,))
TRANSACTION_ARGUMENTS_SCHEMA = build_object_schema(
    {TRANSACTION_ID_FIELD: build_identifier_schema(TRANSACTION_ID_PATTERN, "For example TX-1001.")},
    (TRANSACTION_ID_FIELD,))


def build_tool(name: str, description: str, input_schema: dict, validate_arguments: Callable,
               plan_disclosure: Callable, validate_response: Callable) -> ToolDefinition:
    return ToolDefinition(
        name=name, description=description, input_schema=input_schema,
        allowed_roles=frozenset({ANALYST_ROLE}), validate_arguments=validate_arguments,
        plan_disclosure=plan_disclosure, validate_response=validate_response,
    )


def tool_definitions() -> tuple[ToolDefinition, ...]:
    # Listing tools come before the lookups whose names overlap theirs, and statistics come last: the stub chat
    # model picks the tool sharing the most words with the message and breaks ties by order.
    return (
        build_tool(CUSTOMERS_TOOL,
                   "List customers (id, segment, home branch, customer since), optionally for one branch or "
                   "segment. Anonymized: no names or contact details. Returns at most 50 rows.",
                   CUSTOMER_FILTER_SCHEMA, parse_customer_filter_arguments, plan_nothing,
                   partial(validate_rows, CUSTOMER_LISTING_FIELDS)),
        build_tool(TRANSACTIONS_TOOL,
                   "List transactions (id, date, amount, direction, channel, counterparty type, branch, anomaly "
                   "pattern if flagged), filtered by branch, channel, direction, minimum amount or date range, "
                   "newest or largest first. Anonymized: no account or customer. Returns at most 50 rows.",
                   TRANSACTION_FILTER_SCHEMA, parse_transaction_filter_arguments, plan_nothing,
                   partial(validate_rows, TRANSACTION_FIELDS)),
        build_tool(TRANSACTION_ANOMALIES_TOOL,
                   "List the newest anomalous transactions (id, date, amount, branch, detected pattern), "
                   "optionally for one branch, one pattern or a date range. Anonymized. Returns at most 50 rows.",
                   ANOMALY_FILTER_SCHEMA, parse_anomaly_filter_arguments, plan_nothing,
                   partial(validate_rows, ANOMALY_FIELDS)),
        build_tool(BLOCKED_ACCOUNTS_TOOL,
                   "List the most recently blocked accounts with masked numbers and the blocking reason. Anonymized.",
                   NO_ARGUMENTS_SCHEMA, parse_no_arguments, plan_nothing,
                   partial(validate_rows, BLOCKED_ACCOUNT_FIELDS)),
        build_tool(AML_CASES_TOOL,
                   "List AML (anti-money-laundering) cases (case id, status, risk level, opening date, number of "
                   "linked transactions), newest first, optionally by status or risk level. Anonymized: does not "
                   "name the customers.",
                   AML_CASE_FILTER_SCHEMA, parse_aml_case_filter_arguments, plan_nothing,
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
        build_tool(CUSTOMER_PROFILE_TOOL,
                   "Get a customer's profile by customer id: segment, home branch, customer since, account types "
                   "with opening dates, and number of transactions. No name or contact details.",
                   CUSTOMER_ARGUMENTS_SCHEMA, parse_customer_arguments, plan_nothing,
                   partial(validate_record, CUSTOMER_PROFILE_FIELDS, CUSTOMER_ID_FIELD, CUSTOMER_ID_FIELD)),
        build_tool(CUSTOMER_TRANSACTIONS_TOOL,
                   "List one customer's newest transactions by customer id (id, date, amount, direction, channel, "
                   "counterparty type, anomaly pattern if flagged). Returns at most 50 rows.",
                   CUSTOMER_ARGUMENTS_SCHEMA, parse_customer_arguments,
                   partial(plan_customer_disclosure, TRANSACTION_HISTORY_RELATION), validate_customer_transactions),
        build_tool(TRANSACTION_DETAILS_TOOL,
                   "Get the details of one transaction by transaction id (date, amount, direction, channel, "
                   "counterparty type, branch, anomaly pattern if flagged). Anonymized: no account or customer.",
                   TRANSACTION_ARGUMENTS_SCHEMA, parse_transaction_arguments, plan_nothing,
                   partial(validate_record, TRANSACTION_FIELDS, "id", TRANSACTION_ID_FIELD)),
        build_tool(STATISTICS_TOOL,
                   "Get bank statistics, for the whole bank or one branch: customers by segment and branch, "
                   "transaction count and volume per month, transactions by channel, anomalies by pattern, AML cases "
                   "by status and risk level, blocked accounts by reason. Use it for 'how many' and totals questions.",
                   BRANCH_FILTER_SCHEMA, parse_branch_filter_arguments, plan_nothing, validate_statistics),
    )


def build_registry() -> ToolRegistry:
    return ToolRegistry(tool_definitions())


def build_executor(db_path: str | Path) -> Callable[[str, dict], Any]:
    """The middleware's executor: runs a validated tool call as a read-only query on the demo database."""
    database = BankDemoDatabase(db_path)
    # Argument names match the query methods' parameters, so validated arguments pass straight through.
    queries: dict[str, Callable[..., Any]] = {
        CUSTOMERS_TOOL: database.list_customers,
        TRANSACTIONS_TOOL: database.list_transactions,
        TRANSACTION_ANOMALIES_TOOL: database.list_transaction_anomalies,
        BLOCKED_ACCOUNTS_TOOL: database.list_blocked_accounts,
        AML_CASES_TOOL: database.list_aml_cases,
        AML_CASE_SUMMARY_TOOL: database.get_aml_case,
        CUSTOMER_CONTACT_TOOL: database.get_customer_contact,
        CUSTOMER_WORKPLACE_TOOL: database.get_customer_workplace,
        CUSTOMER_PROFILE_TOOL: database.get_customer_profile,
        CUSTOMER_TRANSACTIONS_TOOL: database.list_customer_transactions,
        TRANSACTION_DETAILS_TOOL: database.get_transaction_details,
        STATISTICS_TOOL: database.get_statistics,
    }

    def execute(tool: str, args: dict) -> Any:
        return queries[tool](**args)

    return execute
