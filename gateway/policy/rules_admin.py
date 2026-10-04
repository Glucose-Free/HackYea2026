"""Dashboard administration of checkpoint 2's Datalog rules.

The data MCP server reloads the rules file on every tool call, so a save here takes effect on the next data fetch
without a restart. Validation is the policy engine's own, so the dashboard cannot save a file the engine rejects.
"""
import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from policy_engine.middleware import PolicyConfigStore, PolicyError, parse_policy_payload

REVISION_LENGTH = 12
# Same rules, same version: switching a rule off and on again returns to the version the audit already knows.
SAVED_VERSION_FORMAT = "dashboard-{digest}"
STALE_REVISION_ERROR = "the rules changed since revision {expected}; reload and try again"


class InvalidPolicyError(ValueError):
    pass


class StalePolicyRevisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class PolicyRulesSnapshot:
    version: str
    # Hash of the file as stored; a save must name the revision it was based on.
    revision: str
    rules: list[dict[str, Any]]


class PolicyRulesAdmin:
    def __init__(self, rules_path: Path, default_rules_path: Path):
        self._config_store = PolicyConfigStore(str(rules_path))
        self._rules_path = rules_path
        self._default_rules_path = default_rules_path
        # Guards the revision check and the write together; the data server only ever reads the file.
        self._write_lock = threading.Lock()

    def get_rules(self) -> PolicyRulesSnapshot:
        content = self._rules_path.read_bytes()
        version, rules = parse_policy_payload(json.loads(content))
        return PolicyRulesSnapshot(version, get_revision(content), [rule.to_dict() for rule in rules])

    def replace_rules(self, rules: list[dict[str, Any]], expected_revision: str) -> PolicyRulesSnapshot:
        with self._write_lock:
            current_revision = get_revision(self._rules_path.read_bytes())
            if current_revision != expected_revision:
                raise StalePolicyRevisionError(STALE_REVISION_ERROR.format(expected=expected_revision))
            self._save(rules)
        return self.get_rules()

    def restore_default_rules(self) -> PolicyRulesSnapshot:
        default_payload = json.loads(self._default_rules_path.read_bytes())
        version, rules = parse_policy_payload(default_payload)
        with self._write_lock:
            self._config_store.save(list(rules), version)
        return self.get_rules()

    def _save(self, rules: list[dict[str, Any]]) -> None:
        version = SAVED_VERSION_FORMAT.format(digest=get_rules_digest(rules))
        try:
            _, parsed_rules = parse_policy_payload({"version": version, "rules": rules})
        except (PolicyError, TypeError, KeyError) as error:
            raise InvalidPolicyError(str(error)) from error
        self._config_store.save(list(parsed_rules), version)


def get_revision(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:REVISION_LENGTH]


def get_rules_digest(rules: list[dict[str, Any]]) -> str:
    canonical = json.dumps(rules, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:REVISION_LENGTH]
