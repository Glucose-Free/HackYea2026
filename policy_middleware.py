"""Stateful disclosure control for read-only MCP tools (Python 3.11+, standard library).

The authenticated backend supplies TrustedPrincipal and a trusted executor. Model-provided
facts are rejected. Only approved tool-result facts enter durable recipient knowledge.
Tool schemas, disclosure plans and permissions belong to trusted domain adapters.
This is a bounded positive-Datalog prototype, not a general privacy compliance engine.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from itertools import islice, product
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from typing import Any, Callable, Iterable, Literal
from types import MappingProxyType
from uuid import uuid4


DecisionOutcome = Literal["allow", "block"]
DEFAULT_DB_PATH = os.environ.get("POLICY_DB_PATH", "policy_knowledge.sqlite3")
DEFAULT_RULES_PATH = os.environ.get("POLICY_RULES_PATH", "policy_rules.json")
PUBLIC_BLOCK_REASON = "Operation unavailable in the current authorization scope."
ARITIES = {"knows": 4, "violation": 3}


class PolicyError(ValueError):
    pass


class InferenceLimit(PolicyError):
    pass


class LegacyDatabaseError(PolicyError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, name: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise PolicyError(f"Invalid {name}")
    return value


def _exact_fields(data: Any, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(data, dict) or not required <= data.keys() or data.keys() - required - optional:
        raise PolicyError("Missing or unsupported fields")
    return data


def _variable(term: str) -> bool:
    return term.startswith("?")


# Public validation helpers for domain modules. They contain no domain assumptions.
validate_text = _text
require_fields = _exact_fields


@dataclass(frozen=True)
class TrustedPrincipal:
    """Construct ONLY from verified authentication, never from model arguments or request JSON."""
    tenant_id: str
    user_id: str
    role: str
    dataset_id: str

    def __post_init__(self):
        for name in ("tenant_id", "user_id", "role", "dataset_id"):
            _text(getattr(self, name), name)

    @property
    def scope(self) -> str:
        encoded = json.dumps([self.tenant_id, self.user_id, self.dataset_id], separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True)
class MCPRequest:
    request_id: str
    session_id: str
    request_text: str = ""  # Not persisted: prompts can contain secrets.
    user_id: str | None = None  # Compatibility hint only; cannot supply authentication.
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)  # Never grants access; not persisted.
    facts: list[dict[str, Any]] = field(default_factory=list)  # Nonempty input is rejected.


@dataclass(frozen=True)
class KnowledgeFact:
    subject: str
    relation: str
    value: str
    source: str = "tool_result"

    def __post_init__(self):
        for name in ("subject", "relation", "value", "source"):
            term = _text(getattr(self, name), name, 256)
            if _variable(term):
                raise PolicyError("Knowledge facts must be ground constants")

    def to_atom(self, scope: str) -> DatalogAtom:
        return DatalogAtom("knows", (scope, self.subject, self.relation, self.value))


@dataclass(frozen=True)
class DatalogAtom:
    predicate: str
    terms: tuple[str, ...]

    def __post_init__(self):
        if self.predicate not in ARITIES or len(self.terms) != ARITIES[self.predicate]:
            raise PolicyError("Unsupported predicate or arity")
        for term in self.terms:
            _text(term, "term", 256)
            if _variable(term) and not re.fullmatch(r"\?[A-Za-z][A-Za-z0-9_]*", term):
                raise PolicyError("Invalid variable")

    def to_dict(self) -> dict:
        return {"predicate": self.predicate, "terms": list(self.terms)}

    @staticmethod
    def from_dict(data: dict) -> DatalogAtom:
        _exact_fields(data, {"predicate", "terms"})
        if not isinstance(data["terms"], list):
            raise PolicyError("Terms must be a list")
        return DatalogAtom(data["predicate"], tuple(data["terms"]))


@dataclass(frozen=True)
class DatalogRule:
    name: str
    head: DatalogAtom
    body: tuple[DatalogAtom, ...]

    def __post_init__(self):
        _text(self.name, "rule name")
        if not 1 <= len(self.body) <= 8:
            raise PolicyError("Rules require between one and eight body atoms")
        variables = {t for atom in self.body for t in atom.terms if _variable(t)}
        if any(_variable(t) and t not in variables for t in self.head.terms):
            raise PolicyError("Unsafe rule: an unbound variable occurs in the head")
        if self.head.predicate == "knows" and self.head.terms[0] != "?u":
            raise PolicyError("Derived knowledge must preserve the current recipient")
        if self.head.predicate == "violation" and self.head.terms[0] != "?u":
            raise PolicyError("Violations must identify the current recipient")
        if any(atom.terms[0] != "?u" for atom in self.body):
            raise PolicyError("All body atoms must use the same recipient variable ?u")

    def to_dict(self) -> dict:
        return {"name": self.name, "head": self.head.to_dict(),
                "body": [atom.to_dict() for atom in self.body]}

    @staticmethod
    def from_dict(data: dict) -> DatalogRule:
        _exact_fields(data, {"name", "head", "body"})
        if not isinstance(data["body"], list):
            raise PolicyError("Rule body must be a list")
        return DatalogRule(data["name"], DatalogAtom.from_dict(data["head"]),
                           tuple(DatalogAtom.from_dict(a) for a in data["body"]))


@dataclass(frozen=True)
class PolicyRuleConfig:
    rule_id: str
    rule: DatalogRule
    enabled: bool = True

    def __post_init__(self):
        _text(self.rule_id, "rule ID")
        if type(self.enabled) is not bool:
            raise PolicyError("Enabled must be a JSON boolean")

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "enabled": self.enabled, "rule": self.rule.to_dict()}

    @staticmethod
    def from_dict(data: dict) -> PolicyRuleConfig:
        _exact_fields(data, {"rule_id", "rule", "enabled"})
        return PolicyRuleConfig(data["rule_id"], DatalogRule.from_dict(data["rule"]), data["enabled"])


@dataclass(frozen=True)
class Proof:
    rule_name: str | None
    parents: tuple[DatalogAtom, ...] = ()
    request_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Inference:
    atoms: frozenset[DatalogAtom]
    proofs: dict[DatalogAtom, Proof]

    def evidence(self, atom: DatalogAtom) -> dict:
        visited: set[DatalogAtom] = set()
        sources: set[str] = set()
        rules: set[str] = set()
        def visit(current):
            if current in visited:
                return
            visited.add(current)
            proof = self.proofs[current]
            sources.update(proof.request_ids)
            if proof.rule_name:
                rules.add(proof.rule_name)
            for parent in proof.parents:
                visit(parent)
        visit(atom)
        # Admin evidence contains IDs and rule names, not raw tool response data.
        return {"subject": atom.terms[1], "rule_ids": sorted(rules),
                "request_ids": sorted(sources)}


@dataclass
class DatalogProgram:
    rules: list[DatalogRule] = field(default_factory=list)
    max_facts: int = 10000
    max_matches: int = 100000
    timeout_seconds: float = 2.0

    def infer(self, facts: Iterable[DatalogAtom]) -> set[DatalogAtom]:
        return set(self.infer_with_proofs({atom: frozenset() for atom in facts}).atoms)

    def infer_with_proofs(self, facts: dict[DatalogAtom, frozenset[str]], *, deadline: float | None = None) -> Inference:
        if len(facts) > self.max_facts:
            raise InferenceLimit("Too many facts")
        if any(_variable(t) for atom in facts for t in atom.terms):
            raise PolicyError("Input facts must be ground")
        proofs = {atom: Proof(None, request_ids=ids) for atom, ids in facts.items()}
        deadline = deadline if deadline is not None else time.monotonic() + self.timeout_seconds
        matches = 0
        changed = True
        while changed:
            changed = False
            index: dict[str, list[DatalogAtom]] = {}
            term_index: dict[tuple[str, int, str], list[DatalogAtom]] = {}
            for atom in sorted(proofs, key=lambda a: (a.predicate, a.terms)):
                index.setdefault(atom.predicate, []).append(atom)
                for position, term in enumerate(atom.terms):
                    term_index.setdefault((atom.predicate, position, term), []).append(atom)
            for rule in self.rules:
                environments = [({}, ())]
                for pattern in rule.body:
                    next_environments = []
                    for environment, parents in environments:
                        buckets = [index.get(pattern.predicate, [])]
                        for position, term in enumerate(pattern.terms):
                            bound = environment.get(term) if _variable(term) else term
                            if bound is not None:
                                buckets.append(term_index.get((pattern.predicate, position, bound), []))
                        for fact in min(buckets, key=len):
                            matches += 1
                            if matches > self.max_matches or time.monotonic() > deadline:
                                raise InferenceLimit("Inference resource limit")
                            merged = dict(environment)
                            for left, right in zip(pattern.terms, fact.terms):
                                if _variable(left):
                                    if left in merged and merged[left] != right:
                                        break
                                    merged[left] = right
                                elif left != right:
                                    break
                            else:
                                next_environments.append((merged, (*parents, fact)))
                    environments = next_environments
                    if not environments:
                        break
                for environment, parents in environments:
                    head = DatalogAtom(rule.head.predicate, tuple(
                        environment[t] if _variable(t) else t for t in rule.head.terms))
                    if head not in proofs:
                        if len(proofs) >= self.max_facts:
                            raise InferenceLimit("Too many derived facts")
                        proofs[head] = Proof(rule.name, parents)
                        changed = True
        return Inference(frozenset(proofs), proofs)


def parse_policy_payload(payload: Any, max_rules: int = 512) -> tuple[str, tuple[PolicyRuleConfig, ...]]:
    """Validates a policy document; the specific PolicyError is for a trusted administrator, never the model."""
    _exact_fields(payload, {"version", "rules"})
    version = _text(payload["version"], "policy version")
    if not isinstance(payload["rules"], list) or not 1 <= len(payload["rules"]) <= max_rules:
        raise PolicyError("Invalid number of rules")
    rules = tuple(PolicyRuleConfig.from_dict(item) for item in payload["rules"])
    if len({r.rule_id for r in rules}) != len(rules):
        raise PolicyError("Duplicate rule IDs")
    if not any(r.enabled and r.rule.head.predicate == "violation" for r in rules):
        raise PolicyError("An enabled blocking rule is required")
    return version, rules


class PolicyConfigStore:
    """No automatic creation or silent defaults; bootstrap is an explicit trusted action."""
    def __init__(self, path: str = DEFAULT_RULES_PATH, *, max_rules: int = 512):
        self.path = Path(path)
        if type(max_rules) is not int or max_rules < 1:
            raise PolicyError("Invalid rule limit")
        self.max_rules = max_rules

    def load(self) -> tuple[str, tuple[PolicyRuleConfig, ...]]:
        try:
            return parse_policy_payload(json.loads(self.path.read_text(encoding="utf-8")), self.max_rules)
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise PolicyError("Invalid or missing policy configuration") from error

    def save(self, rules: list[PolicyRuleConfig], version: str) -> None:
        # Validate before replacing. Atomic rename prevents readers seeing partial JSON.
        payload = {"version": version, "rules": [r.to_dict() for r in rules]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".policy-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            PolicyConfigStore(temporary, max_rules=self.max_rules).load()
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def initialize(self, rules: list[PolicyRuleConfig], version: str) -> None:
        """Explicit trusted bootstrap, with no built-in domain policy."""
        if self.path.exists():
            self.load()
            return
        self.save(rules, version)


@dataclass(frozen=True)
class MCPDecision:
    outcome: DecisionOutcome
    reason: str = ""
    data: Any | None = None
    tags: tuple[str, ...] = ()
    # Audit-only: the trusted host may record it, but as_dict() leaves it out so the model never learns
    # which rule fired. Empty for refusals that are not policy violations (bad arguments, failures).
    violated_rule_ids: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"

    def as_dict(self) -> dict:
        return {"outcome": self.outcome, "allowed": self.allowed,
                "reason": self.reason, "data": self.data, "tags": list(self.tags)}


def _blocked(violated_rule_ids: tuple[str, ...] = ()) -> MCPDecision:
    return MCPDecision("block", PUBLIC_BLOCK_REASON, tags=("policy_block",), violated_rule_ids=violated_rule_ids)


class KnowledgeStore:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = str(db_path)
        if self.db_path == ":memory:":
            raise PolicyError("A persistent database path is required")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {"requests", "knowledge_facts"} & tables:
                raise LegacyDatabaseError("Legacy history is ambiguous; migrate verified disclosures explicitly")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS disclosure_requests (
                    scope TEXT NOT NULL, request_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL, session_id TEXT NOT NULL, tool_name TEXT NOT NULL,
                    signature TEXT NOT NULL, created_at TEXT NOT NULL, outcome TEXT NOT NULL,
                    policy_version TEXT NOT NULL, internal_code TEXT NOT NULL,
                    evidence_json TEXT NOT NULL, reply_json TEXT NOT NULL,
                    PRIMARY KEY(scope, request_id)
                );
                CREATE TABLE IF NOT EXISTS approved_facts (
                    scope TEXT NOT NULL, request_id TEXT NOT NULL, subject TEXT NOT NULL,
                    relation TEXT NOT NULL, value TEXT NOT NULL, source TEXT NOT NULL,
                    PRIMARY KEY(scope, request_id, subject, relation, value)
                );
                CREATE INDEX IF NOT EXISTS approved_facts_scope ON approved_facts(scope);
            """)
        if os.name == "posix":
            os.chmod(self.db_path, 0o600)

    @contextmanager
    def transaction(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def load_facts(conn, principal: TrustedPrincipal) -> dict[KnowledgeFact, frozenset[str]]:
        found: dict[KnowledgeFact, frozenset[str]] = {}
        for row in conn.execute("SELECT * FROM approved_facts WHERE scope = ?", (principal.scope,)):
            fact = KnowledgeFact(row["subject"], row["relation"], row["value"], row["source"])
            found[fact] = found.get(fact, frozenset()) | {row["request_id"]}
        return found

    def facts_for_user(self, principal: TrustedPrincipal) -> list[KnowledgeFact]:
        if not isinstance(principal, TrustedPrincipal):
            raise PolicyError("Authenticated principal required")
        with self.transaction() as conn:
            return list(self.load_facts(conn, principal))

    def audit_events(self, principal: TrustedPrincipal) -> list[dict]:
        if not isinstance(principal, TrustedPrincipal) or principal.role != "security_admin":
            raise PolicyError("Security administrator required")
        with self.transaction() as conn:
            rows = conn.execute("""SELECT request_id, user_id, session_id, tool_name, created_at,
                    outcome, policy_version, internal_code, evidence_json
                    FROM disclosure_requests WHERE tenant_id = ? ORDER BY created_at""",
                    (principal.tenant_id,)).fetchall()
            return [dict(row) for row in rows]


@dataclass(frozen=True)
class ValueDomain:
    """Possible unknown values, declared by a trusted adapter BEFORE reading private data.

    Either an explicit finite set, a full-match pattern, or all ground strings.
    Used in any fact position; the planner conservatively expands public equalities.
    """
    values: frozenset[str] | None = None
    pattern: str | None = None

    def __post_init__(self):
        if self.values is not None:
            if isinstance(self.values, str):
                raise PolicyError("A value domain requires a collection of constants")
            values = frozenset(self.values)
            if not 1 <= len(values) <= 1000 or self.pattern is not None:
                raise PolicyError("Invalid finite value domain")
            for value in values:
                _text(value, "domain constant", 256)
                if _variable(value):
                    raise PolicyError("Value domains must contain ground constants")
            object.__setattr__(self, "values", values)
        elif self.pattern is not None:
            _text(self.pattern, "domain pattern", 256)
            try:
                re.compile(self.pattern)
            except re.error as error:
                raise PolicyError("Invalid domain pattern") from error

    def covers(self, value: str) -> bool:
        if self.values is not None:
            return value in self.values
        return self.pattern is None or re.fullmatch(self.pattern, value) is not None


@dataclass(frozen=True)
class PlannedFact:
    subject: str | ValueDomain
    relation: str | ValueDomain
    value: str | ValueDomain

    def __post_init__(self):
        for term in (self.subject, self.relation, self.value):
            if not isinstance(term, ValueDomain):
                _text(term, "planned term", 256)
                if _variable(term):
                    raise PolicyError("Planned facts must be ground or use a value domain")

    def covers(self, fact: KnowledgeFact) -> bool:
        return all(left.covers(right) if isinstance(left, ValueDomain) else left == right
                   for left, right in zip((self.subject, self.relation, self.value),
                                          (fact.subject, fact.relation, fact.value)))


@dataclass(frozen=True)
class ToolDefinition:
    """Trusted host registration: schema + ACL + pure planner + strict result validator.

    Validation callbacks implement domain semantics; the core does not interpret
    arbitrary JSON Schema. Never create registrations from MCP arguments or LLM output.
    """
    name: str
    description: str
    input_schema: dict
    allowed_roles: frozenset[str]
    validate_arguments: Callable[[dict], dict]
    plan_disclosure: Callable[[dict, tuple[KnowledgeFact, ...]], Iterable[PlannedFact | KnowledgeFact]]
    validate_response: Callable[[dict, Any], tuple[Any, Iterable[KnowledgeFact]]]
    _schema_json: str = field(init=False, repr=False)

    def __post_init__(self):
        if not isinstance(self.name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", self.name):
            raise PolicyError("Invalid tool name")
        _text(self.description, "tool description", 4000)
        if isinstance(self.allowed_roles, str):
            raise PolicyError("Roles must be an explicit collection")
        roles = frozenset(self.allowed_roles)
        if not roles:
            raise PolicyError("Tool permissions must be explicit")
        for role in roles:
            _text(role, "role")
        object.__setattr__(self, "allowed_roles", roles)
        if not all(callable(value) for value in (self.validate_arguments, self.plan_disclosure, self.validate_response)):
            raise PolicyError("Every tool requires validation and a disclosure plan")
        try:
            encoded = json.dumps(self.input_schema, allow_nan=False)
            schema = json.loads(encoded)
            if (not isinstance(schema, dict) or schema.get("type") != "object"
                    or schema.get("additionalProperties") is not False
                    or not isinstance(schema.get("properties", {}), dict)
                    or not isinstance(schema.get("required", []), list)
                    or not all(isinstance(key, str) and key in schema.get("properties", {})
                               for key in schema.get("required", []))):
                raise PolicyError("A closed object input schema is required")
            object.__setattr__(self, "_schema_json", encoded)
        except (TypeError, ValueError) as error:
            raise PolicyError("Invalid input schema") from error

    def descriptor(self) -> dict:
        return {"name": self.name, "description": self.description,
                "inputSchema": json.loads(self._schema_json),
                "annotations": {"readOnlyHint": True, "destructiveHint": False,
                                "idempotentHint": False, "openWorldHint": False}}


class ToolRegistry:
    """Immutable catalog shared by policy checks and MCP discovery."""
    def __init__(self, tools: Iterable[ToolDefinition], *, max_tools: int = 512,
                 max_result_bytes: int = 65536, max_disclosure_facts: int = 1000):
        for limit in (max_tools, max_result_bytes, max_disclosure_facts):
            if type(limit) is not int or limit < 1:
                raise PolicyError("Invalid registry limit")
        entries = list(islice(iter(tools), max_tools + 1))
        if not entries or len(entries) > max_tools or not all(isinstance(t, ToolDefinition) for t in entries):
            raise PolicyError("Invalid tool catalog")
        if len({t.name for t in entries}) != len(entries):
            raise PolicyError("Duplicate tool name")
        self._tools = MappingProxyType({t.name: t for t in entries})
        self.max_result_bytes = max_result_bytes
        self.max_disclosure_facts = max_disclosure_facts

    def authorize(self, name: str, principal: TrustedPrincipal) -> ToolDefinition:
        tool = self._tools.get(name)
        if not isinstance(principal, TrustedPrincipal) or tool is None or principal.role not in tool.allowed_roles:
            raise PolicyError("Tool unavailable in this scope")
        return tool

    def names_for(self, principal: TrustedPrincipal) -> frozenset[str]:
        if not isinstance(principal, TrustedPrincipal):
            return frozenset()
        return frozenset(name for name, tool in self._tools.items() if principal.role in tool.allowed_roles)

    def list_tools(self, principal: TrustedPrincipal) -> list[dict]:
        names = self.names_for(principal)
        return [tool.descriptor() for name, tool in self._tools.items() if name in names]

    def list_tools_for_role(self, role: str) -> list[dict]:
        """For a server whose callers all share one role, so the catalog is known before any caller is."""
        return [tool.descriptor() for tool in self._tools.values() if role in tool.allowed_roles]

    def _tool(self, name: str) -> ToolDefinition:
        if name not in self._tools:
            raise PolicyError("Unregistered tool")
        return self._tools[name]

    def arguments(self, name: str, arguments: dict) -> dict:
        if not isinstance(arguments, dict):
            raise PolicyError("Arguments must be an object")
        args = self._tool(name).validate_arguments(dict(arguments))
        if not isinstance(args, dict):
            raise PolicyError("Argument validator must return an object")
        return self._json_copy(args)

    def plan(self, name: str, args: dict, history: Iterable[KnowledgeFact]) -> list[PlannedFact]:
        facts = list(islice(iter(self._tool(name).plan_disclosure(dict(args), tuple(history))),
                            self.max_disclosure_facts + 1))
        if len(facts) > self.max_disclosure_facts:
            raise PolicyError("Too many planned disclosures")
        normalized = []
        for fact in facts:
            if isinstance(fact, KnowledgeFact):
                fact = PlannedFact(fact.subject, fact.relation, fact.value)
            if not isinstance(fact, PlannedFact):
                raise PolicyError("Invalid disclosure plan")
            normalized.append(fact)
        return normalized

    def _json_copy(self, value: Any) -> Any:
        try:
            encoded = json.dumps(value, allow_nan=False, ensure_ascii=False)
            if len(encoded.encode("utf-8")) > self.max_result_bytes:
                raise PolicyError("JSON payload too large")
            return json.loads(encoded)
        except (ValueError, TypeError, UnicodeError, RecursionError) as error:
            raise PolicyError("Invalid JSON payload") from error

    def disclosure(self, name: str, args: dict, result: Any) -> tuple[Any, list[KnowledgeFact]]:
        data, disclosures = self._tool(name).validate_response(dict(args), self._json_copy(result))
        facts = list(islice(iter(disclosures), self.max_disclosure_facts + 1))
        if len(facts) > self.max_disclosure_facts or not all(isinstance(f, KnowledgeFact) for f in facts):
            raise PolicyError("Invalid extracted disclosures")
        return self._json_copy(data), facts


class PolicyMiddleware:
    def __init__(self, store: KnowledgeStore, config_store: PolicyConfigStore, registry: ToolRegistry):
        if not isinstance(registry, ToolRegistry):
            raise PolicyError("An explicit trusted tool registry is required")
        self.store = store
        self.config_store = config_store
        self.registry = registry

    @staticmethod
    def _inference(program, principal, history, candidate, request_id):
        deadline = time.monotonic() + program.timeout_seconds
        expansions = 0
        atoms: dict[DatalogAtom, frozenset[str]] = {}
        for fact, ids in history.items():
            atom = fact.to_atom(principal.scope)
            atoms[atom] = atoms.get(atom, frozenset()) | ids
        # Expand unknowns from adapter-declared domains using only public constants.
        # A shared fresh representative also covers equalities between unknown values.
        # Private results must not influence preflight; refusals could otherwise become an oracle.
        candidate = [PlannedFact(f.subject, f.relation, f.value) if isinstance(f, KnowledgeFact) else f
                     for f in candidate]
        constants = {term for atom in atoms for term in atom.terms}
        constants.update(term for rule in program.rules for atom in (rule.head, *rule.body)
                         for term in atom.terms if not _variable(term))
        for fact in candidate:
            for term in (fact.subject, fact.relation, fact.value):
                if isinstance(term, str):
                    constants.add(term)
                elif term.values is not None:
                    constants.update(term.values)
        fresh = "__unknown_" + uuid4().hex
        while fresh in constants:
            fresh = "__unknown_" + uuid4().hex
        for fact in candidate:
            domains = []
            for term in (fact.subject, fact.relation, fact.value):
                if isinstance(term, str):
                    domains.append((term,))
                elif term.values is not None:
                    domains.append(tuple(sorted(term.values)))
                else:
                    # The synthetic representative is intentionally shared even across patterns;
                    # that overapproximates unknown equalities, sometimes causing extra refusals.
                    domains.append(tuple(sorted({fresh} | {c for c in constants if term.covers(c)})))
            for subject, relation, value in product(*domains):
                expansions += 1
                if expansions > program.max_matches or time.monotonic() > deadline:
                    raise InferenceLimit("Disclosure expansion resource limit")
                atom = KnowledgeFact(subject, relation, value).to_atom(principal.scope)
                atoms[atom] = atoms.get(atom, frozenset()) | {request_id}
                if len(atoms) > program.max_facts:
                    raise InferenceLimit("Too many planned facts")
        inference = program.infer_with_proofs(atoms, deadline=deadline)
        violations = sorted((a for a in inference.atoms if a.predicate == "violation"
                             and a.terms[0] == principal.scope), key=lambda a: a.terms)
        return inference.evidence(violations[0]) if violations else None

    def handle(self, request: MCPRequest, *, principal: TrustedPrincipal | None = None,
               execute: Callable[[str, dict], Any] | None = None) -> MCPDecision:
        """Atomically check, execute a READ-ONLY tool, check output, commit, and then return.

        This intentionally changes the old API: there is no safe automatic trust in request.user_id
        or request.facts. Uncertain delivery retains released knowledge. Executors must be read-only.
        """
        if not isinstance(principal, TrustedPrincipal) or not isinstance(request, MCPRequest):
            return _blocked()
        try:
            _text(request.request_id, "request ID")
            _text(request.session_id, "session ID")
            tool = _text(request.tool_name, "tool name")
        except PolicyError:
            return _blocked()
        untrusted_fields = bool(request.facts) or (
            request.user_id is not None and request.user_id != principal.user_id)
        try:
            signature = hashlib.sha256(json.dumps(
                {"tool": tool, "arguments": request.arguments, "role": principal.role,
                 "untrusted_fields": untrusted_fields},
                sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
        except (ValueError, TypeError):
            return _blocked()
        with self.store.transaction() as conn:
            previous = conn.execute("SELECT * FROM disclosure_requests WHERE scope=? AND request_id=?",
                                    (principal.scope, request.request_id)).fetchone()
            if previous:
                if previous["signature"] != signature:
                    return _blocked()
                try:
                    self.registry.authorize(tool, principal)
                except PolicyError:
                    return _blocked()
                cached = json.loads(previous["reply_json"])
                return MCPDecision(cached["outcome"], cached["reason"], cached["data"], tuple(cached["tags"]))
            reply = _blocked()
            version, internal_code, evidence = "unavailable", "policy_unavailable", {}
            approved: list[KnowledgeFact] = []
            try:
                if untrusted_fields:
                    internal_code = "untrusted_request_fields"
                    raise PolicyError("The request cannot supply facts or a different identity")
                version, rules = self.config_store.load()
                program = DatalogProgram([r.rule for r in rules if r.enabled])
                internal_code = "invalid_tool_request"
                self.registry.authorize(tool, principal)
                args = self.registry.arguments(tool, request.arguments)
                history = self.store.load_facts(conn, principal)
                candidate = self.registry.plan(tool, args, history)
                internal_code = "inference_failed"
                evidence = self._inference(program, principal, history, candidate, request.request_id) or {}
                if evidence:
                    internal_code = "preflight_violation"
                    reply = _blocked(tuple(evidence["rule_ids"]))
                else:
                    internal_code = "tool_execution_failed"
                    if execute is None:
                        raise PolicyError("A trusted read-only executor is required")
                    result = execute(tool, dict(args))
                    internal_code = "invalid_tool_response"
                    data, actual_facts = self.registry.disclosure(tool, args, result)
                    if any(not any(planned.covers(fact) for planned in candidate) for fact in actual_facts):
                        raise PolicyError("Response contains an undeclared disclosure")
                    internal_code = "inference_failed"
                    evidence = self._inference(program, principal, history, actual_facts, request.request_id) or {}
                    if evidence:
                        internal_code = "postflight_violation"
                        reply = _blocked(tuple(evidence["rule_ids"]))
                    else:
                        approved = actual_facts
                        reply = MCPDecision("allow", "Approved disclosure", data, ("policy_allow",))
                        internal_code = "allowed"
            except Exception:
                # Do not return exception details, private tool responses, or rule IDs to the model.
                reply, approved, evidence = _blocked(), [], {}
            conn.execute("""INSERT INTO disclosure_requests
                (scope,request_id,tenant_id,user_id,session_id,tool_name,signature,created_at,outcome,
                 policy_version,internal_code,evidence_json,reply_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (principal.scope, request.request_id, principal.tenant_id, principal.user_id,
                 request.session_id, tool, signature, _utc_now(), reply.outcome, version, internal_code,
                 json.dumps(evidence), json.dumps(reply.as_dict(), allow_nan=False)))
            for fact in approved:
                conn.execute("INSERT OR IGNORE INTO approved_facts VALUES (?,?,?,?,?,?)",
                             (principal.scope, request.request_id, fact.subject, fact.relation,
                              fact.value, fact.source))
        # The transaction commits before any result is returned to the caller.
        return reply
