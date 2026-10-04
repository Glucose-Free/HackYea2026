"""Example: an employee may learn at most one party of the same transaction."""
from __future__ import annotations
from functools import partial
import re

from policy_engine.middleware import (
    DatalogAtom, DatalogRule, KnowledgeFact, KnowledgeStore, PlannedFact, PolicyConfigStore,
    PolicyError, PolicyMiddleware, PolicyRuleConfig, ToolDefinition, ToolRegistry,
    TrustedPrincipal, ValueDomain, require_fields, validate_text,
)

RELATIONS = {
    "get_transaction_sender": "transaction.sender",
    "get_transaction_recipient": "transaction.recipient",
}


def default_rules() -> list[PolicyRuleConfig]:
    return [PolicyRuleConfig("block_both_transaction_parties", DatalogRule(
        "block_both_transaction_parties",
        DatalogAtom("violation", ("?u", "?transaction", "both_parties")),
        (DatalogAtom("knows", ("?u", "?transaction", "transaction.sender", "?sender")),
         DatalogAtom("knows", ("?u", "?transaction", "transaction.recipient", "?recipient"))),
    ))]


def validate_arguments(arguments: dict) -> dict:
    require_fields(arguments, {"transaction_id"})
    value = arguments["transaction_id"]
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise PolicyError("Invalid transaction ID")
    return {"transaction_id": value}


def plan(name: str, args: dict, history) -> list[PlannedFact]:
    return [PlannedFact(args["transaction_id"], RELATIONS[name], ValueDomain())]


def validate_response(name: str, args: dict, data: dict) -> tuple[dict, list[KnowledgeFact]]:
    require_fields(data, {"transaction_id", "company_id"})
    if data["transaction_id"] != args["transaction_id"]:
        raise PolicyError("Response refers to a different transaction")
    validate_text(data["company_id"], "company ID", 256)
    fact = KnowledgeFact(args["transaction_id"], RELATIONS[name], data["company_id"])
    return data, [fact]


def tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(ToolDefinition(
        name=name, description="Read one party of a transaction, subject to disclosure policy.",
        input_schema={"type": "object", "additionalProperties": False,
                      "properties": {"transaction_id": {"type": "string", "minLength": 1,
                          "maxLength": 64, "pattern": r"^[A-Za-z0-9_-]{1,64}$"}},
                      "required": ["transaction_id"]},
        allowed_roles=frozenset({"transaction_reader"}),
        validate_arguments=validate_arguments,
        plan_disclosure=partial(plan, name),
        validate_response=partial(validate_response, name),
    ) for name in RELATIONS)


def build_registry() -> ToolRegistry:
    return ToolRegistry(tool_definitions())


def initialize_demo(db_path: str, rules_path: str) -> PolicyMiddleware:
    config = PolicyConfigStore(rules_path)
    config.initialize(default_rules(), "transactions-v1")
    return PolicyMiddleware(KnowledgeStore(db_path), config, build_registry())


def demo_executor(name: str, args: dict) -> dict:
    if name not in RELATIONS:
        raise PolicyError("Unknown demo tool")
    company = "company-acme" if name == "get_transaction_sender" else "company-beta"
    return {**args, "company_id": company}


def create_demo_backend():
    principal = TrustedPrincipal("fictional-bank", "demo-employee",
                                 role="transaction_reader", dataset_id="transactions-demo-v1")
    return principal, build_registry(), demo_executor


def main():
    import argparse
    from policy_engine.mcp_stdio_server import MCPPolicyServer
    parser = argparse.ArgumentParser(description="Run the fictional transaction-party example")
    parser.add_argument("--db", required=True)
    parser.add_argument("--rules", required=True)
    options = parser.parse_args()
    middleware = initialize_demo(options.db, options.rules)
    principal, _, execute = create_demo_backend()
    MCPPolicyServer(middleware, principal=principal, execute=execute).serve_stdio()


if __name__ == "__main__":
    main()
