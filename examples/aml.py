"""Example domain: AML mosaic restrictions. Not imported by the generic core or MCP."""
from __future__ import annotations
from functools import partial
from typing import Any, Iterable
import re

from policy_engine.middleware import (
    DatalogAtom, DatalogRule, KnowledgeFact, KnowledgeStore, PlannedFact, PolicyConfigStore,
    PolicyError, PolicyMiddleware, PolicyRuleConfig, ToolDefinition, ToolRegistry,
    TrustedPrincipal, ValueDomain, require_fields, validate_text,
)

def default_rules() -> list[PolicyRuleConfig]:
    k = lambda *terms: DatalogAtom("knows", tuple(terms))
    rows = [PolicyRuleConfig("identify_via_account", DatalogRule(
        "identify_via_account", k("?u", "?s", "identity", "?c"),
        (k("?u", "?s", "subject_account", "?a"), k("?u", "?a", "account_owner", "?c"))))]
    rows.append(PolicyRuleConfig("block_aml_identity", DatalogRule("block_aml_identity",
        DatalogAtom("violation", ("?u", "?s", "block_aml_identity")),
        (k("?u", "?s", "aml_review", "?status"), k("?u", "?s", "identity", "?c")))))
    # Contact and workplace facts are keyed by customer, not AML subject, so they join through identity.
    # They only matter when block_aml_identity is disabled; they keep each disclosure independently blockable.
    for relation in ("contact_data", "workplace_data"):
        name = f"block_aml_{relation}"
        rows.append(PolicyRuleConfig(name, DatalogRule(name,
            DatalogAtom("violation", ("?u", "?s", name)),
            (k("?u", "?s", "aml_review", "?status"),
             k("?u", "?s", "identity", "?c"),
             k("?u", "?c", relation, "?value")))))
    return rows


def _id(value: Any, prefix: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(prefix + r"[0-9]{1,12}", value):
        raise PolicyError("Invalid or unsupported demo entity ID")
    return value


class AMLToolAdapter:
    """Trusted, closed schemas. Register a new adapter before adding any new MCP tool.

    Planning uses only public arguments and prior released facts, never private result values.
    The executor's output is validated with a strict schema and registered after approval.
    """
    TOOLS = {
        "get_transaction_anomalies": ("subject_id", "S"),
        "get_blocked_accounts": ("subject_id", "S"),
        "get_aml_case_summary": ("subject_id", "S"),
        "resolve_aml_subject": ("subject_id", "S"),
        "get_account_owner": ("account_id", "A"),
        "get_customer_contact": ("customer_id", "C"),
        "get_customer_workplace": ("customer_id", "C"),
    }

    def arguments(self, tool: str, arguments: dict) -> dict:
        if tool not in self.TOOLS:
            raise PolicyError("Unregistered tool")
        key, prefix = self.TOOLS[tool]
        require_fields(arguments, {key})
        return {key: _id(arguments[key], prefix)}

    def plan(self, tool: str, args: dict, history: Iterable[KnowledgeFact]) -> list[PlannedFact | KnowledgeFact]:
        subject = next(iter(args.values()))
        if tool == "get_aml_case_summary":
            return [PlannedFact(subject, "aml_review", ValueDomain(values=frozenset({"true", "false"})))]
        if tool == "resolve_aml_subject":
            return [PlannedFact(subject, "identity", ValueDomain(pattern=r"C[0-9]{1,12}"))]
        if tool == "get_account_owner":
            return [PlannedFact(subject, "account_owner", ValueDomain(pattern=r"C[0-9]{1,12}"))]
        if tool == "get_customer_contact":
            return [KnowledgeFact(subject, "contact_data", "disclosed")]
        if tool == "get_customer_workplace":
            return [KnowledgeFact(subject, "workplace_data", "disclosed")]
        if tool == "get_blocked_accounts":
            return [PlannedFact(subject, "subject_account", ValueDomain(pattern=r"A[0-9]{1,12}"))]
        return []  # Strict anomaly response contains aggregate pattern counts only.

    def disclosure(self, tool: str, args: dict, result: Any) -> tuple[dict, list[KnowledgeFact]]:
        data = result  # Registry has already detached and size-limited JSON.
        key, _ = self.TOOLS[tool]
        subject = args[key]
        base = {key}
        facts: list[KnowledgeFact] = []
        if tool == "get_aml_case_summary":
            require_fields(data, base | {"under_review"})
            if type(data["under_review"]) is not bool:
                raise PolicyError("under_review must be boolean")
            facts.append(KnowledgeFact(subject, "aml_review", str(data["under_review"]).lower()))
        elif tool in {"resolve_aml_subject", "get_account_owner"}:
            require_fields(data, base | {"customer_id"})
            customer = _id(data["customer_id"], "C")
            relation = "identity" if tool == "resolve_aml_subject" else "account_owner"
            facts.append(KnowledgeFact(subject, relation, customer))
        elif tool == "get_customer_contact":
            require_fields(data, base | {"phone", "address"})
            validate_text(data["phone"], "phone", 100)
            validate_text(data["address"], "address", 1000)
            facts.append(KnowledgeFact(subject, "contact_data", "disclosed"))
        elif tool == "get_customer_workplace":
            require_fields(data, base | {"employer"})
            validate_text(data["employer"], "employer", 1000)
            facts.append(KnowledgeFact(subject, "workplace_data", "disclosed"))
        elif tool == "get_blocked_accounts":
            require_fields(data, base | {"accounts"})
            if not isinstance(data["accounts"], list) or len(data["accounts"]) > 100:
                raise PolicyError("Invalid account list")
            for account in data["accounts"]:
                require_fields(account, {"account_id", "blocked"})
                account_id = _id(account["account_id"], "A")
                if type(account["blocked"]) is not bool:
                    raise PolicyError("blocked must be boolean")
                facts.append(KnowledgeFact(subject, "subject_account", account_id))
        elif tool == "get_transaction_anomalies":
            require_fields(data, base | {"anomalies"})
            if not isinstance(data["anomalies"], list) or len(data["anomalies"]) > 100:
                raise PolicyError("Invalid anomaly list")
            for anomaly in data["anomalies"]:
                require_fields(anomaly, {"pattern", "count"})
                if anomaly["pattern"] not in {"structuring", "rapid_movement", "circular_transfers"}:
                    raise PolicyError("Unsupported pattern")
                if type(anomaly["count"]) is not int or not 0 <= anomaly["count"] <= 1000000:
                    raise PolicyError("Invalid aggregate count")
        if data[key] != subject:
            raise PolicyError("Response refers to a different entity")
        return data, facts




def tool_definitions() -> tuple[ToolDefinition, ...]:
    adapter = AMLToolAdapter()
    descriptions = {
        "get_transaction_anomalies": "Read aggregate anomaly counts for an AML subject.",
        "get_blocked_accounts": "Read account identifiers and blocked status for an AML subject.",
        "get_aml_case_summary": "Read protected review status of an AML subject.",
        "resolve_aml_subject": "Read the customer identity corresponding to an AML subject.",
        "get_account_owner": "Read the customer owning an account.",
        "get_customer_contact": "Read a customer's phone and address.",
        "get_customer_workplace": "Read a customer's employer.",
    }
    tools = []
    for name, (field, prefix) in adapter.TOOLS.items():
        tools.append(ToolDefinition(
            name=name, description=descriptions[name],
            input_schema={"type": "object", "additionalProperties": False,
                          "properties": {field: {"type": "string", "pattern": "^" + prefix + r"[0-9]{1,12}$",
                                                 "minLength": 2, "maxLength": 13}}, "required": [field]},
            allowed_roles=frozenset({"restricted_analyst"}),
            validate_arguments=partial(adapter.arguments, name),
            plan_disclosure=partial(adapter.plan, name),
            validate_response=partial(adapter.disclosure, name),
        ))
    return tuple(tools)


def build_registry() -> ToolRegistry:
    return ToolRegistry(tool_definitions())


def initialize_demo(db_path: str, rules_path: str) -> PolicyMiddleware:
    config = PolicyConfigStore(rules_path)
    config.initialize(default_rules(), "aml-v3")
    return PolicyMiddleware(KnowledgeStore(db_path), config, build_registry())


def demo_executor(tool: str, args: dict) -> dict:
    """Fictional fixture data for this explicitly selected example."""
    if tool == "get_aml_case_summary":
        return {**args, "under_review": True}
    if tool in {"resolve_aml_subject", "get_account_owner"}:
        return {**args, "customer_id": "C17"}
    if tool == "get_blocked_accounts":
        return {**args, "accounts": [{"account_id": "A9", "blocked": True}]}
    if tool == "get_customer_contact":
        return {**args, "phone": "+000000000", "address": "Fictional address"}
    if tool == "get_customer_workplace":
        return {**args, "employer": "Fictional employer"}
    if tool == "get_transaction_anomalies":
        return {**args, "anomalies": [{"pattern": "structuring", "count": 4}]}
    raise ValueError("Unknown demo tool")


def create_demo_backend():
    """Fictional example factory, selected explicitly by the trusted host."""
    principal = TrustedPrincipal("fictional-bank", "demo-employee",
                                 role="restricted_analyst", dataset_id="bank-demo-v1")
    return principal, build_registry(), demo_executor


def main():
    import argparse
    from policy_engine.mcp_stdio_server import MCPPolicyServer
    parser = argparse.ArgumentParser(description="Run the fictional AML example")
    parser.add_argument("--db", required=True)
    parser.add_argument("--rules", required=True)
    options = parser.parse_args()
    middleware = initialize_demo(options.db, options.rules)
    principal, _, execute = create_demo_backend()
    MCPPolicyServer(middleware, principal=principal, execute=execute).serve_stdio()


if __name__ == "__main__":
    main()
