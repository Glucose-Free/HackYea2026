"""Cross-session Datalog policy handling for MCP requests.

User knowledge is persisted in SQLite and accumulated across sessions.
This is a small boilerplate that can later be swapped to a real database
without changing the policy surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from contextlib import contextmanager
import json
import os
import sqlite3
from typing import Any, Iterable, Literal


DecisionOutcome = Literal["allow", "block"]
DEFAULT_DB_PATH = os.environ.get(
    "POLICY_DB_PATH",
    os.path.join(os.path.dirname(__file__), "policy_knowledge.sqlite3"),
)
DEFAULT_RULES_PATH = os.environ.get(
    "POLICY_RULES_PATH",
    os.path.join(os.path.dirname(__file__), "policy_rules.json"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_variable(term: str) -> bool:
    return term.startswith("?")


@dataclass(frozen=True)
class MCPRequest:
    request_id: str
    session_id: str
    request_text: str
    user_id: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    facts: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)


@dataclass(frozen=True)
class KnowledgeFact:
    subject: str
    relation: str
    value: str
    source: str = "request"

    def key(self) -> str:
        return f"{self.subject}|{self.relation}|{self.value}|{self.source}"

    def to_atom(self, user_id: str | None) -> "DatalogAtom":
        return DatalogAtom("knows", (user_id or "unknown_user", self.relation, self.value))

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "KnowledgeFact":
        return KnowledgeFact(
            subject=str(data.get("subject", "")),
            relation=str(data.get("relation", "")),
            value=str(data.get("value", "")),
            source=str(data.get("source", "request")),
        )


@dataclass(frozen=True)
class DatalogAtom:
    predicate: str
    terms: tuple[str, ...]

    def ground(self) -> str:
        return f"{self.predicate}({', '.join(self.terms)})"

    def to_dict(self) -> dict[str, Any]:
        return {"predicate": self.predicate, "terms": list(self.terms)}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "DatalogAtom":
        return DatalogAtom(
            predicate=str(data.get("predicate", "")),
            terms=tuple(str(term) for term in data.get("terms", [])),
        )


@dataclass(frozen=True)
class DatalogRule:
    name: str
    head: DatalogAtom
    body: tuple[DatalogAtom, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "head": self.head.to_dict(),
            "body": [atom.to_dict() for atom in self.body],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "DatalogRule":
        return DatalogRule(
            name=str(data.get("name", "unnamed_rule")),
            head=DatalogAtom.from_dict(data.get("head", {})),
            body=tuple(DatalogAtom.from_dict(item) for item in data.get("body", [])),
        )


@dataclass(frozen=True)
class PolicyRuleConfig:
    rule_id: str
    enabled: bool = True
    rule: DatalogRule = field(default_factory=lambda: DatalogRule("unnamed_rule", DatalogAtom("decision", ("allow", "default")), tuple()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "enabled": self.enabled,
            "rule": self.rule.to_dict(),
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "PolicyRuleConfig":
        return PolicyRuleConfig(
            rule_id=str(data.get("rule_id", "unnamed_rule")),
            enabled=bool(data.get("enabled", True)),
            rule=DatalogRule.from_dict(data.get("rule", {})),
        )


@dataclass
class DatalogProgram:
    facts: set[DatalogAtom] = field(default_factory=set)
    rules: list[DatalogRule] = field(default_factory=list)

    def add_rule(self, rule: DatalogRule) -> None:
        self.rules.append(rule)

    def infer(self, facts: Iterable[DatalogAtom] | None = None) -> set[DatalogAtom]:
        known = set(self.facts)
        if facts is not None:
            known.update(facts)

        changed = True
        while changed:
            changed = False
            for rule in self.rules:
                for env in self._match_rule(rule, known):
                    head = self._ground(rule.head, env)
                    if head is None:
                        continue
                    if head not in known:
                        known.add(head)
                        changed = True
        return known

    def _match_rule(self, rule: DatalogRule, known: set[DatalogAtom]) -> list[dict[str, str]]:
        envs: list[dict[str, str]] = [{}]
        for atom in rule.body:
            next_envs: list[dict[str, str]] = []
            for env in envs:
                pattern = self._ground(atom, env, keep_unbound=True)
                if pattern is None:
                    continue
                for fact in known:
                    merged = self._unify(pattern, fact, env)
                    if merged is not None:
                        next_envs.append(merged)
            envs = next_envs
            if not envs:
                break
        return envs

    @staticmethod
    def _unify(pattern: DatalogAtom, fact: DatalogAtom, env: dict[str, str]) -> dict[str, str] | None:
        if pattern.predicate != fact.predicate or len(pattern.terms) != len(fact.terms):
            return None

        merged = dict(env)
        for pattern_term, fact_term in zip(pattern.terms, fact.terms):
            if _is_variable(pattern_term):
                bound = merged.get(pattern_term)
                if bound is None:
                    merged[pattern_term] = fact_term
                elif bound != fact_term:
                    return None
            elif pattern_term != fact_term:
                return None
        return merged

    @staticmethod
    def _ground(atom: DatalogAtom, env: dict[str, str], keep_unbound: bool = False) -> DatalogAtom | None:
        terms: list[str] = []
        for term in atom.terms:
            if _is_variable(term):
                if term not in env:
                    if keep_unbound:
                        terms.append(term)
                        continue
                    return None
                terms.append(env[term])
            else:
                terms.append(term)
        return DatalogAtom(atom.predicate, tuple(terms))


class PolicyConfigStore:
    def __init__(self, path: str = DEFAULT_RULES_PATH) -> None:
        self.path = path
        self._ensure_default_file()

    def _ensure_default_file(self) -> None:
        if os.path.exists(self.path):
            return
        self.save(self.default_rules())

    def load(self) -> list[PolicyRuleConfig]:
        with open(self.path, encoding="utf-8") as handle:
            payload = json.load(handle)
        return [PolicyRuleConfig.from_dict(item) for item in payload.get("rules", [])]

    def save(self, rules: list[PolicyRuleConfig]) -> None:
        payload = {
            "version": 1,
            "rules": [rule.to_dict() for rule in rules],
            "updated_at": _utc_now(),
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def list_rules(self) -> list[PolicyRuleConfig]:
        return self.load()

    def upsert_rule(self, rule: PolicyRuleConfig) -> None:
        rules = self.load()
        for index, existing in enumerate(rules):
            if existing.rule_id == rule.rule_id:
                rules[index] = rule
                self.save(rules)
                return
        rules.append(rule)
        self.save(rules)

    def set_rule_enabled(self, rule_id: str, enabled: bool) -> None:
        rules = self.load()
        for index, existing in enumerate(rules):
            if existing.rule_id == rule_id:
                rules[index] = PolicyRuleConfig(rule_id=existing.rule_id, enabled=enabled, rule=existing.rule)
                self.save(rules)
                return
        raise KeyError(f"Unknown rule: {rule_id}")

    @staticmethod
    def default_rules() -> list[PolicyRuleConfig]:
        return [
            PolicyRuleConfig(
                rule_id="block_aml_contact",
                enabled=True,
                rule=DatalogRule(
                    name="block_aml_contact",
                    head=DatalogAtom("decision", ("block", "aml_contact")),
                    body=(
                        DatalogAtom("knows", ("?user", "aml_flag", "true")),
                        DatalogAtom("knows", ("?user", "contact_data", "true")),
                    ),
                ),
            ),
            PolicyRuleConfig(
                rule_id="block_aml_workplace",
                enabled=True,
                rule=DatalogRule(
                    name="block_aml_workplace",
                    head=DatalogAtom("decision", ("block", "aml_workplace")),
                    body=(
                        DatalogAtom("knows", ("?user", "aml_flag", "true")),
                        DatalogAtom("knows", ("?user", "workplace_data", "true")),
                    ),
                ),
            ),
        ]


@dataclass
class MCPDecision:
    outcome: DecisionOutcome = "allow"
    reason: str = ""
    data: Any | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.outcome != "block"

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "allowed": self.allowed,
            "reason": self.reason,
            "data": self.data,
            "tags": self.tags,
        }


class KnowledgeStore:
    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self._ensure_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_id TEXT,
                    request_text TEXT NOT NULL,
                    tool_name TEXT,
                    arguments_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    session_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, session_id, request_id, subject, relation, value, source)
                )
                """
            )

    def add_request(self, request: MCPRequest) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO requests
                (request_id, session_id, user_id, request_text, tool_name, arguments_json, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.request_id,
                    request.session_id,
                    request.user_id,
                    request.request_text,
                    request.tool_name,
                    json.dumps(request.arguments, ensure_ascii=False),
                    json.dumps(request.metadata, ensure_ascii=False),
                    request.created_at,
                ),
            )

    def add_facts(self, request: MCPRequest, facts: Iterable[KnowledgeFact]) -> None:
        with self._connect() as conn:
            for fact in facts:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO knowledge_facts
                    (user_id, session_id, request_id, subject, relation, value, source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.user_id,
                        request.session_id,
                        request.request_id,
                        fact.subject,
                        fact.relation,
                        fact.value,
                        fact.source,
                        request.created_at,
                    ),
                )

    def facts_for_user(self, user_id: str | None) -> list[KnowledgeFact]:
        with self._connect() as conn:
            if user_id is None:
                rows = conn.execute(
                    "SELECT subject, relation, value, source FROM knowledge_facts"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT subject, relation, value, source FROM knowledge_facts WHERE user_id = ?",
                    (user_id,),
                ).fetchall()
        return [KnowledgeFact(row[0], row[1], row[2], row[3]) for row in rows]

    def session_ids_for_user(self, user_id: str | None) -> list[str]:
        with self._connect() as conn:
            if user_id is None:
                rows = conn.execute("SELECT DISTINCT session_id FROM knowledge_facts").fetchall()
            else:
                rows = conn.execute(
                    "SELECT DISTINCT session_id FROM knowledge_facts WHERE user_id = ?",
                    (user_id,),
                ).fetchall()
        return [row[0] for row in rows]

    def summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            fact_count = conn.execute("SELECT COUNT(*) FROM knowledge_facts").fetchone()[0]
            request_count = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        return {"fact_count": fact_count, "request_count": request_count, "db_path": self.db_path}


def infer_facts_from_request(request: MCPRequest) -> list[KnowledgeFact]:
    facts: list[KnowledgeFact] = [KnowledgeFact.from_dict(item) for item in request.facts]
    tool_name = (request.tool_name or "").strip()
    arguments = request.arguments or {}

    if tool_name == "get_transaction_anomalies":
        facts.append(KnowledgeFact("session", "transaction_anomaly", "true", source="tool"))
        facts.append(KnowledgeFact("session", "anonymized_context", "true", source="tool"))
    elif tool_name == "get_blocked_accounts":
        facts.append(KnowledgeFact("session", "blocked_account", "true", source="tool"))
        facts.append(KnowledgeFact("session", "anonymized_context", "true", source="tool"))
    elif tool_name == "get_aml_case_summary":
        facts.append(KnowledgeFact("session", "aml_flag", "true", source="tool"))
        facts.append(KnowledgeFact("session", "anonymized_context", "true", source="tool"))
    elif tool_name == "get_customer_contact":
        facts.append(KnowledgeFact(str(arguments.get("customer_id", "customer")), "contact_data", "true", source="tool"))
    elif tool_name == "get_customer_workplace":
        facts.append(KnowledgeFact(str(arguments.get("customer_id", "customer")), "workplace_data", "true", source="tool"))

    return facts


def build_program_from_config(rules: Iterable[PolicyRuleConfig]) -> DatalogProgram:
    program = DatalogProgram()
    for entry in rules:
        if entry.enabled:
            program.add_rule(entry.rule)
    return program


class PolicyMiddleware:
    """Adapter between MCP requests, SQLite knowledge and Datalog."""

    def __init__(
        self,
        store: KnowledgeStore | None = None,
        config_store: PolicyConfigStore | None = None,
        program: DatalogProgram | None = None,
    ) -> None:
        self.store = store or KnowledgeStore()
        self.config_store = config_store or PolicyConfigStore()
        self.program = program or build_program_from_config(self.config_store.list_rules())

    def reload_rules(self) -> None:
        self.program = build_program_from_config(self.config_store.list_rules())

    def handle(self, request: MCPRequest) -> MCPDecision:
        facts = infer_facts_from_request(request)
        self.store.add_request(request)
        self.store.add_facts(request, facts)
        self.reload_rules()
        return self.decide(request)

    def decide(self, request: MCPRequest) -> MCPDecision:
        all_facts = self.store.facts_for_user(request.user_id)
        atoms = [fact.to_atom(request.user_id) for fact in all_facts]
        inferred = self.program.infer(atoms)

        block_atoms = sorted(
            (atom for atom in inferred if atom.predicate == "decision" and atom.terms and atom.terms[0] == "block"),
            key=lambda atom: atom.terms[1] if len(atom.terms) > 1 else atom.terms[0],
        )
        if block_atoms:
            reasons = [atom.terms[1] if len(atom.terms) > 1 else "blocked" for atom in block_atoms]
            return MCPDecision(outcome="block", reason=", ".join(reasons), tags=["datalog_block", *reasons])

        return MCPDecision(outcome="allow", reason="no datalog rule matched", tags=["datalog_allow"])

    def add_rule(self, rule: DatalogRule) -> None:
        rule_id = rule.name
        self.config_store.upsert_rule(PolicyRuleConfig(rule_id=rule_id, enabled=True, rule=rule))
        self.reload_rules()

    def set_rule_enabled(self, rule_id: str, enabled: bool) -> None:
        self.config_store.set_rule_enabled(rule_id, enabled)
        self.reload_rules()

    def list_rules(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self.config_store.list_rules()]

    def snapshot(self, session_id: str) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "store": self.store.summary(),
            "session_ids": self.store.session_ids_for_user(None),
            "rules": self.list_rules(),
        }


DEFAULT_MIDDLEWARE = PolicyMiddleware()
