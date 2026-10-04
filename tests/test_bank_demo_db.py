import sqlite3
from pathlib import Path

import pytest

from examples import bank_demo
from examples.bank_demo_db import FEATURED_AML_CASE_ID, MAX_RETURNED_ROWS, BankDemoDatabase
from examples.bank_demo_seed import create_bank_demo_database, ensure_bank_demo_database
from policy_middleware import PolicyConfigStore


@pytest.fixture(scope="module")
def bank_db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("bank") / "bank_demo.sqlite3"
    create_bank_demo_database(path)
    return path


def dump_database(path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return list(connection.iterdump())
    finally:
        connection.close()


def test_the_generated_database_is_identical_on_every_build(bank_db_path, tmp_path):
    rebuilt_path = tmp_path / "rebuilt.sqlite3"
    create_bank_demo_database(rebuilt_path)
    assert dump_database(rebuilt_path) == dump_database(bank_db_path)


def test_the_demo_storyline_is_kept_verbatim(bank_db_path):
    database = BankDemoDatabase(bank_db_path)
    assert database.get_aml_case(FEATURED_AML_CASE_ID) == {
        "case_id": FEATURED_AML_CASE_ID, "status": "under investigation", "risk_level": "high",
        "opened_on": "2026-10-01", "customer_id": "CUST-17", "linked_transactions": ["TX-1001", "TX-1003"],
    }
    assert database.get_customer_contact("CUST-17")["phone"] == "+48 601 234 567"
    assert database.get_customer_workplace("CUST-17")["employer"] == "Kowalski Logistics sp. z o.o."
    assert {"PL-****-4411", "PL-****-9032"} <= {row["account"] for row in database.list_blocked_accounts()}


def test_the_ids_named_in_the_readme_still_match(bank_db_path):
    database = BankDemoDatabase(bank_db_path)
    assert database.get_aml_case("AML-2026-0007")["customer_id"] == "CUST-53"
    assert "CUST-1" not in {database.get_aml_case(row["case_id"])["customer_id"] for row in database.list_aml_cases()}


def test_listings_are_capped(bank_db_path):
    database = BankDemoDatabase(bank_db_path)
    for rows in (database.list_customers(), database.list_transaction_anomalies(), database.list_aml_cases()):
        assert 0 < len(rows) <= MAX_RETURNED_ROWS


def test_a_missing_record_raises_lookup_error(bank_db_path):
    with pytest.raises(LookupError):
        BankDemoDatabase(bank_db_path).get_customer_contact("CUST-99999")


def test_a_missing_database_fails_instead_of_being_created(tmp_path):
    missing_path = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        BankDemoDatabase(missing_path).list_blocked_accounts()
    assert not missing_path.exists()


def test_the_connection_cannot_write(bank_db_path):
    connection = BankDemoDatabase(bank_db_path)._connect()
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM customers")
    finally:
        connection.close()


def test_ensure_keeps_an_existing_database(tmp_path):
    path = tmp_path / "bank_demo.sqlite3"
    path.write_bytes(b"existing")
    ensure_bank_demo_database(path)
    assert path.read_bytes() == b"existing"


def test_the_shipped_rules_file_matches_the_default_rules():
    version, rules = PolicyConfigStore(str(Path(bank_demo.__file__).with_name("bank_demo_rules.json"))).load()
    assert version == bank_demo.RULES_VERSION
    assert list(rules) == bank_demo.default_rules()
