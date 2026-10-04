import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deploy.policy_mcp.server import BANK_DEMO_RULES_PATH, build_policy_data_server, ensure_policy_rules
from examples.bank_demo_seed import create_bank_demo_database
from gateway.agent.tools import McpToolProvider, ToolCallContext, ToolCaller
from gateway.app import create_app
from gateway.policy.rules_admin import PolicyRulesAdmin
from tests.fakes import FakeChatModel
from tests.test_admin_api import REPORT_TOKEN, build_client, build_components

RULES_PATH = "/admin/policy/rules"
RESTORE_PATH = "/admin/policy/rules/restore-defaults"
HEADERS = {"X-Report-Token": REPORT_TOKEN}
TRACEPARENT = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
ALICE_CONTEXT = ToolCallContext(TRACEPARENT, ToolCaller("u-alice", "chat-1"))
CONTACT_WORKPLACE_RULE = {
    "rule_id": "contact_workplace", "enabled": True,
    "rule": {
        "name": "contact_workplace",
        "head": {"predicate": "violation", "terms": ["?u", "?c", "contact_workplace"]},
        "body": [{"predicate": "knows", "terms": ["?u", "?c", "contact_data", "?a"]},
                 {"predicate": "knows", "terms": ["?u", "?c", "workplace_data", "?b"]}],
    },
}


@pytest.fixture
def rules_path(tmp_path: Path) -> Path:
    path = tmp_path / "rules" / "policy_rules.json"
    ensure_policy_rules(str(path))
    return path


@pytest.fixture
def client(tmp_path: Path, rules_path: Path):
    components = replace(build_components(tmp_path, FakeChatModel([])),
                         policy_rules_admin=PolicyRulesAdmin(rules_path, Path(BANK_DEMO_RULES_PATH)))
    with TestClient(create_app(components)) as test_client:
        yield test_client


def get_rules(client: TestClient) -> dict:
    response = client.get(RULES_PATH, headers=HEADERS)
    assert response.status_code == 200
    return response.json()


def put_rules(client: TestClient, rules: list, revision: str):
    return client.put(RULES_PATH, headers=HEADERS, json={"revision": revision, "rules": rules})


def test_lists_the_live_rules(client: TestClient):
    policy = get_rules(client)
    assert policy["version"] == "bank-demo-v2"
    assert {rule["rule_id"] for rule in policy["rules"]} == {"aml_contact", "aml_workplace", "history_contact", "history_workplace"}


def test_saving_adds_a_rule_and_stamps_a_new_version(client: TestClient, rules_path: Path):
    policy = get_rules(client)
    response = put_rules(client, [*policy["rules"], CONTACT_WORKPLACE_RULE], policy["revision"])
    assert response.status_code == 200
    saved = response.json()
    assert saved["version"].startswith("dashboard-") and saved["revision"] != policy["revision"]
    assert "contact_workplace" in {rule["rule_id"] for rule in json.loads(rules_path.read_text())["rules"]}


def test_the_same_rules_get_the_same_version(client: TestClient):
    policy = get_rules(client)
    disabled_rules = copy.deepcopy(policy["rules"])
    disabled_rules[0]["enabled"] = False
    disabled = put_rules(client, disabled_rules, policy["revision"]).json()
    enabled_again = put_rules(client, policy["rules"], disabled["revision"]).json()
    original_rules_saved = put_rules(client, policy["rules"], enabled_again["revision"]).json()
    assert enabled_again["version"] == original_rules_saved["version"] != disabled["version"]


def test_an_invalid_policy_is_rejected_with_the_engines_reason(client: TestClient, rules_path: Path):
    policy = get_rules(client)
    all_disabled = [{**rule, "enabled": False} for rule in policy["rules"]]
    response = put_rules(client, all_disabled, policy["revision"])
    assert response.status_code == 400
    assert response.json()["detail"] == "An enabled blocking rule is required"
    unsafe_rule = copy.deepcopy(CONTACT_WORKPLACE_RULE)
    unsafe_rule["rule"]["head"]["terms"][1] = "?unbound"
    assert "unbound variable" in put_rules(client, [unsafe_rule], policy["revision"]).json()["detail"]
    assert get_rules(client)["revision"] == policy["revision"]


def test_a_save_based_on_an_old_revision_is_refused(client: TestClient):
    policy = get_rules(client)
    assert put_rules(client, [*policy["rules"], CONTACT_WORKPLACE_RULE], policy["revision"]).status_code == 200
    assert put_rules(client, policy["rules"], policy["revision"]).status_code == 409


def test_restore_defaults_brings_back_the_shipped_rules(client: TestClient):
    policy = get_rules(client)
    put_rules(client, [CONTACT_WORKPLACE_RULE], policy["revision"])
    restored = client.post(RESTORE_PATH, headers=HEADERS).json()
    assert restored["version"] == "bank-demo-v2"
    assert [rule["rule_id"] for rule in restored["rules"]] == [rule["rule_id"] for rule in policy["rules"]]


def test_without_a_rules_file_the_endpoints_are_unavailable(tmp_path: Path):
    with build_client(tmp_path, FakeChatModel([])) as test_client:
        assert test_client.get(RULES_PATH, headers=HEADERS).status_code == 503


async def test_a_disabled_rule_stops_blocking_on_the_next_fetch(client: TestClient, rules_path: Path, tmp_path: Path):
    bank_db_path = tmp_path / "bank.sqlite3"
    create_bank_demo_database(bank_db_path)
    server = build_policy_data_server(str(tmp_path / "knowledge.sqlite3"), str(bank_db_path), str(rules_path))
    policy = get_rules(client)
    rules_without_contact = [{**rule, "enabled": rule["rule_id"] != "aml_contact"} for rule in policy["rules"]]
    async with McpToolProvider(server).open_session() as session:
        await session.call_tool("get_aml_case_summary", {}, ALICE_CONTEXT)
        denied = await session.call_tool("get_customer_contact", {"customer_id": "CUST-17"}, ALICE_CONTEXT)
        assert put_rules(client, rules_without_contact, policy["revision"]).status_code == 200
        allowed = await session.call_tool("get_customer_contact", {"customer_id": "CUST-17"}, ALICE_CONTEXT)
    assert denied.denied and not allowed.denied
