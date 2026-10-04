"""Builds the bank demo's SQLite database: a few hundred fictional customers with half a year of activity.

Run `python -m examples.bank_demo_seed PATH`. The output is deterministic (fixed seed and as-of date), so every
deployment serves the same data and the README's example IDs stay valid. The demo storyline (CUST-17 under AML case
AML-2026-0042, linked to TX-1001 and TX-1003) is written verbatim; everything else is generated around it, using
ID ranges that cannot collide with it. All people, companies, phone numbers and accounts are made up.
"""
from __future__ import annotations

import argparse
import os
import random
import sqlite3
import unicodedata
from dataclasses import astuple, dataclass, fields
from datetime import date, timedelta
from pathlib import Path

from examples.bank_demo_db import BRANCHES, FEATURED_AML_CASE_ID, SCHEMA

RANDOM_SEED = 2026
AS_OF_DATE = date(2026, 10, 3)
HISTORY_DAYS = 180
HISTORY_START_DATE = AS_OF_DATE - timedelta(days=HISTORY_DAYS)
CUSTOMER_COUNT = 240
GENERATED_AML_CASE_COUNT = 41  # The featured case is AML-2026-0042, so the generated ones are 0001-0041.
GENERATED_BLOCKED_ACCOUNT_COUNT = 18
FIRST_GENERATED_TRANSACTION_NUMBER = 100001
AML_CUSTOMER_ANOMALY_RATE = 0.12
REGULAR_CUSTOMER_ANOMALY_RATE = 0.015
MAX_LINKED_TRANSACTIONS_PER_CASE = 4
# Generated cases open before the featured case, and after at least one of their flagged transactions.
EARLIEST_CASE_DAYS_INTO_HISTORY = 20
MIN_DAYS_FROM_FLAG_TO_CASE = 1
MAX_DAYS_FROM_FLAG_TO_CASE = 10
LATEST_GENERATED_CASE_DATE = date(2026, 9, 30)
TRANSACTIONS_PER_ACCOUNT = (8, 30)
ACCOUNTS_PER_CUSTOMER = (1, 3)
CUSTOMER_SINCE_YEARS = (2004, 2025)
ACCOUNT_NUMBER_DIGITS = 26
COUNTRY_CODE = "PL"
EMAIL_DOMAIN = "example.pl"
CUSTOMER_ID_FORMAT = "CUST-{number}"
TRANSACTION_ID_FORMAT = "TX-{number}"
AML_CASE_ID_FORMAT = "AML-2026-{number:04d}"
TEMPORARY_FILE_SUFFIX = ".tmp"
CREATED_MESSAGE = "wrote the bank demo database to {path}"
ALREADY_EXISTS_ERROR = "{path} already exists; pass --force to rebuild it"

SEGMENT_RETAIL = "retail"
SEGMENT_PREMIUM = "premium"
SEGMENT_BUSINESS = "business"
SEGMENT_WEIGHTS = {SEGMENT_RETAIL: 70, SEGMENT_PREMIUM: 15, SEGMENT_BUSINESS: 15}
ACCOUNT_CURRENT = "current"
ACCOUNT_SAVINGS = "savings"
ACCOUNT_BUSINESS = "business"
DIRECTION_IN = "in"
DIRECTION_OUT = "out"
CHANNEL_CARD = "card"
CHANNEL_TRANSFER = "transfer"
CHANNEL_CASH = "cash"
CHANNEL_BLIK = "blik"

MALE_FIRST_NAMES = ["Jan", "Piotr", "Krzysztof", "Andrzej", "Tomasz", "Paweł", "Michał", "Marcin", "Jakub", "Adam",
                    "Łukasz", "Grzegorz", "Mateusz", "Wojciech", "Marek", "Kamil", "Rafał", "Szymon"]
FEMALE_FIRST_NAMES = ["Anna", "Maria", "Katarzyna", "Małgorzata", "Agnieszka", "Barbara", "Ewa", "Magdalena", "Joanna",
                      "Aleksandra", "Zofia", "Monika", "Natalia", "Karolina", "Julia", "Dorota", "Beata", "Alicja"]
# (male form, female form): Polish surnames ending in -ski/-cki change with gender.
SURNAMES = [("Kowalski", "Kowalska"), ("Nowak", "Nowak"), ("Wiśniewski", "Wiśniewska"), ("Wójcik", "Wójcik"),
            ("Kowalczyk", "Kowalczyk"), ("Kamiński", "Kamińska"), ("Lewandowski", "Lewandowska"),
            ("Zieliński", "Zielińska"), ("Szymański", "Szymańska"), ("Woźniak", "Woźniak"),
            ("Dąbrowski", "Dąbrowska"), ("Kozłowski", "Kozłowska"), ("Jankowski", "Jankowska"), ("Mazur", "Mazur"),
            ("Kwiatkowski", "Kwiatkowska"), ("Krawczyk", "Krawczyk"), ("Piotrowski", "Piotrowska"),
            ("Grabowski", "Grabowska"), ("Nowakowski", "Nowakowska"), ("Pawłowski", "Pawłowska"),
            ("Michalski", "Michalska"), ("Adamczyk", "Adamczyk"), ("Dudek", "Dudek"), ("Zając", "Zając"),
            ("Wieczorek", "Wieczorek"), ("Jabłoński", "Jabłońska"), ("Król", "Król"), ("Majewski", "Majewska")]
EMPLOYERS = ["Wisła Logistics sp. z o.o.", "Bałtyk Software S.A.", "Mazovia Agro sp. z o.o.", "Tatra Energy S.A.",
             "Odra Construction sp. z o.o.", "Warta Retail sp. z o.o.", "Pomorze Shipping S.A.",
             "Sudety Medical Center", "Polonia Insurance S.A.", "Kujawy Food Processing sp. z o.o.",
             "Bieszczady Timber sp. z o.o.", "Silesia Steelworks S.A.", "Notec Telecom S.A.",
             "Mazury Hotels sp. z o.o.", "Vistula Pharma S.A.", "Regional Public Hospital", "Municipal Water Utility",
             "State Primary School No. 12", "Amber Coast Tourism sp. z o.o.", "Karkonosze Automotive S.A."]
BUSINESS_NAME_SUFFIXES = ["Trading sp. z o.o.", "Logistics sp. z o.o.", "Consulting", "Import-Export sp. j.",
                          "Construction sp. z o.o.", "Auto Parts s.c."]
BUSINESS_EMPLOYER_FORMAT = "{surname} {suffix}"


@dataclass(frozen=True)
class AmountTemplate:
    direction: str
    channel: str
    counterparty: str
    minimum_pln: float
    maximum_pln: float
    rounding_digits: int = 2


@dataclass(frozen=True)
class AnomalyTemplate:
    pattern: str
    amount: AmountTemplate


PERSONAL_ACTIVITY = [
    AmountTemplate(DIRECTION_OUT, CHANNEL_CARD, "Grocery store", 15, 450),
    AmountTemplate(DIRECTION_OUT, CHANNEL_CARD, "Fuel station", 80, 400),
    AmountTemplate(DIRECTION_OUT, CHANNEL_BLIK, "Online marketplace", 20, 900),
    AmountTemplate(DIRECTION_OUT, CHANNEL_TRANSFER, "Utility bill", 90, 650),
    AmountTemplate(DIRECTION_OUT, CHANNEL_TRANSFER, "Rent", 1800, 4200),
    AmountTemplate(DIRECTION_OUT, CHANNEL_TRANSFER, "Insurance premium", 60, 400),
    AmountTemplate(DIRECTION_OUT, CHANNEL_CASH, "ATM withdrawal", 100, 1500),
    AmountTemplate(DIRECTION_IN, CHANNEL_TRANSFER, "Salary", 4200, 16000),
    AmountTemplate(DIRECTION_IN, CHANNEL_TRANSFER, "Incoming transfer", 50, 3000),
]
BUSINESS_ACTIVITY = [
    AmountTemplate(DIRECTION_IN, CHANNEL_TRANSFER, "Client invoice payment", 2000, 60000),
    AmountTemplate(DIRECTION_OUT, CHANNEL_TRANSFER, "Supplier payment", 1000, 40000),
    AmountTemplate(DIRECTION_OUT, CHANNEL_TRANSFER, "Payroll", 8000, 90000),
    AmountTemplate(DIRECTION_OUT, CHANNEL_TRANSFER, "Tax office (VAT)", 1500, 30000),
    AmountTemplate(DIRECTION_OUT, CHANNEL_CARD, "Office supplies", 50, 2500),
]
STRUCTURING = "structuring"
RAPID_IN_OUT = "rapid in-out"
ANOMALIES = [
    AnomalyTemplate(STRUCTURING, AmountTemplate(DIRECTION_IN, CHANNEL_CASH, "Cash deposit", 9000, 14900)),
    AnomalyTemplate(RAPID_IN_OUT, AmountTemplate(DIRECTION_IN, CHANNEL_TRANSFER, "Incoming transfer", 30000, 90000)),
    AnomalyTemplate("round-amount cash", AmountTemplate(DIRECTION_IN, CHANNEL_CASH, "Cash deposit", 20000, 60000, -4)),
    AnomalyTemplate("high-risk jurisdiction",
                    AmountTemplate(DIRECTION_OUT, CHANNEL_TRANSFER, "Foreign transfer", 15000, 120000)),
    AnomalyTemplate("dormant account activity",
                    AmountTemplate(DIRECTION_IN, CHANNEL_TRANSFER, "Incoming transfer", 20000, 70000)),
    AnomalyTemplate("velocity spike", AmountTemplate(DIRECTION_OUT, CHANNEL_BLIK, "Multiple small payments", 500, 3000)),
]
AML_STATUS_WEIGHTS = {"open": 30, "under investigation": 30, "escalated to GIIF": 15, "closed - no action": 15,
                      "closed - reported": 10}
RISK_LEVEL_WEIGHTS = {"low": 25, "medium": 45, "high": 30}
SUSPICIOUS_INFLOWS = "suspicious inflows"
COURT_ORDER = "court order"
AML_BLOCK_REASONS = [SUSPICIOUS_INFLOWS, "AML investigation hold"]
OTHER_BLOCK_REASONS = [COURT_ORDER, "bailiff seizure", "customer reported fraud", "identity verification pending"]

# The value domains the data tools accept as filters; derived from the generator so the two cannot drift apart.
SEGMENTS = tuple(SEGMENT_WEIGHTS)
CHANNELS = (CHANNEL_CARD, CHANNEL_TRANSFER, CHANNEL_CASH, CHANNEL_BLIK)
DIRECTIONS = (DIRECTION_IN, DIRECTION_OUT)
ANOMALY_PATTERNS = tuple(anomaly.pattern for anomaly in ANOMALIES)
AML_STATUSES = tuple(AML_STATUS_WEIGHTS)
RISK_LEVELS = tuple(RISK_LEVEL_WEIGHTS)


@dataclass(frozen=True)
class CustomerRow:
    customer_id: str
    full_name: str
    segment: str
    branch: str
    customer_since: str
    phone: str
    email: str
    employer: str
    work_city: str


@dataclass(frozen=True)
class AccountRow:
    account_number: str
    customer_id: str
    account_type: str
    opened_on: str
    blocked_on: str | None = None
    block_reason: str | None = None


@dataclass(frozen=True)
class TransactionRow:
    transaction_id: str
    account_number: str
    booked_on: str
    amount_pln: float
    direction: str
    channel: str
    counterparty: str
    branch: str
    anomaly_pattern: str | None = None


@dataclass(frozen=True)
class AmlCaseRow:
    case_id: str
    customer_id: str
    status: str
    risk_level: str
    opened_on: str


@dataclass(frozen=True)
class AmlCaseTransactionRow:
    case_id: str
    transaction_id: str


@dataclass(frozen=True)
class PendingAmlCase:
    """A case before it gets its number, which follows opening order."""
    customer_id: str
    opened_on: date
    linked_transaction_ids: list[str]


@dataclass
class BankDemoDataset:
    customers: list[CustomerRow]
    accounts: list[AccountRow]
    transactions: list[TransactionRow]
    aml_cases: list[AmlCaseRow]
    aml_case_transactions: list[AmlCaseTransactionRow]


# The storyline the README's demo script and the tests rely on.
STORYLINE_CUSTOMER_ID = "CUST-17"
STORYLINE_BRANCH = "Warsaw"
STORYLINE_CUSTOMER = CustomerRow(STORYLINE_CUSTOMER_ID, "Jan Kowalski", SEGMENT_PREMIUM, STORYLINE_BRANCH,
                                 "2015-03-12", "+48 601 234 567", "j.kowalski@example.pl",
                                 "Kowalski Logistics sp. z o.o.", STORYLINE_BRANCH)
STORYLINE_BLOCKED_ACCOUNT = AccountRow("PL27114020040000300201354411", STORYLINE_CUSTOMER_ID, ACCOUNT_CURRENT,
                                       "2015-03-12", "2026-09-30", SUSPICIOUS_INFLOWS)
STORYLINE_SAVINGS_ACCOUNT = AccountRow("PL61109010140000071219812874", STORYLINE_CUSTOMER_ID, ACCOUNT_SAVINGS,
                                       "2018-06-01")
COURT_ORDER_CUSTOMER_ID = "CUST-203"
COURT_ORDER_ACCOUNT = AccountRow("PL83102055610000310200019032", COURT_ORDER_CUSTOMER_ID, ACCOUNT_CURRENT,
                                 "2019-11-20", "2026-10-01", COURT_ORDER)
STORYLINE_ACCOUNT_NUMBER = STORYLINE_BLOCKED_ACCOUNT.account_number
STORYLINE_TRANSACTIONS = [
    TransactionRow("TX-1001", STORYLINE_ACCOUNT_NUMBER, "2026-09-28", 12500.00, DIRECTION_IN, CHANNEL_CASH,
                   "Cash deposit", STORYLINE_BRANCH, STRUCTURING),
    TransactionRow("TX-1002", STORYLINE_ACCOUNT_NUMBER, "2026-09-29", 9800.00, DIRECTION_IN, CHANNEL_CASH,
                   "Cash deposit", STORYLINE_BRANCH),
    TransactionRow("TX-1003", STORYLINE_ACCOUNT_NUMBER, "2026-09-30", 47000.00, DIRECTION_IN, CHANNEL_TRANSFER,
                   "Incoming transfer", STORYLINE_BRANCH, RAPID_IN_OUT),
    TransactionRow("TX-1004", STORYLINE_ACCOUNT_NUMBER, "2026-09-30", 46500.00, DIRECTION_OUT, CHANNEL_TRANSFER,
                   "Foreign transfer", STORYLINE_BRANCH),
]
STORYLINE_AML_CASE = AmlCaseRow(FEATURED_AML_CASE_ID, STORYLINE_CUSTOMER_ID, "under investigation", "high",
                                "2026-10-01")
STORYLINE_AML_CASE_TRANSACTIONS = [AmlCaseTransactionRow(FEATURED_AML_CASE_ID, "TX-1001"),
                                   AmlCaseTransactionRow(FEATURED_AML_CASE_ID, "TX-1003")]
STORYLINE_ACCOUNTS = [STORYLINE_BLOCKED_ACCOUNT, STORYLINE_SAVINGS_ACCOUNT, COURT_ORDER_ACCOUNT]


def build_bank_demo_dataset(rng: random.Random) -> BankDemoDataset:
    customers = list_generated_customers(rng)
    customer_by_id = {customer.customer_id: customer for customer in customers}
    aml_customer_ids = set(rng.sample(sorted(customer_by_id.keys() - {STORYLINE_CUSTOMER_ID}),
                                      GENERATED_AML_CASE_COUNT))
    taken_account_numbers = {account.account_number for account in STORYLINE_ACCOUNTS}
    accounts = [*STORYLINE_ACCOUNTS]
    for customer in customers:
        accounts.extend(list_customer_accounts(customer, taken_account_numbers, rng))
    transactions = list_generated_transactions(accounts, customer_by_id, aml_customer_ids, rng)
    accounts = block_generated_accounts(accounts, aml_customer_ids, rng)
    aml_cases, aml_case_transactions = list_generated_aml_cases(aml_customer_ids, accounts, transactions, rng)
    return BankDemoDataset(
        customers=customers,
        accounts=accounts,
        transactions=[*STORYLINE_TRANSACTIONS, *transactions],
        aml_cases=[*aml_cases, STORYLINE_AML_CASE],
        aml_case_transactions=[*aml_case_transactions, *STORYLINE_AML_CASE_TRANSACTIONS],
    )


def list_generated_customers(rng: random.Random) -> list[CustomerRow]:
    customers = []
    for number in range(1, CUSTOMER_COUNT + 1):
        customer_id = CUSTOMER_ID_FORMAT.format(number=number)
        customers.append(STORYLINE_CUSTOMER if customer_id == STORYLINE_CUSTOMER_ID
                         else build_customer(customer_id, rng))
    return customers


def build_customer(customer_id: str, rng: random.Random) -> CustomerRow:
    is_female = rng.random() < 0.5
    first_name = rng.choice(FEMALE_FIRST_NAMES if is_female else MALE_FIRST_NAMES)
    male_surname, female_surname = rng.choice(SURNAMES)
    surname = female_surname if is_female else male_surname
    segment = choose_weighted(SEGMENT_WEIGHTS, rng)
    branch = rng.choice(BRANCHES)
    employer = (BUSINESS_EMPLOYER_FORMAT.format(surname=male_surname, suffix=rng.choice(BUSINESS_NAME_SUFFIXES))
                if segment == SEGMENT_BUSINESS else rng.choice(EMPLOYERS))
    # Most people work where they bank; some commute or moved.
    work_city = branch if rng.random() < 0.8 else rng.choice(BRANCHES)
    first_year, last_year = CUSTOMER_SINCE_YEARS
    customer_since = build_random_date(date(first_year, 1, 1), date(last_year, 12, 31), rng)
    return CustomerRow(customer_id, f"{first_name} {surname}", segment, branch, customer_since.isoformat(),
                       build_phone_number(rng), build_email(first_name, surname, customer_id), employer, work_city)


def build_phone_number(rng: random.Random) -> str:
    return f"+48 {rng.choice('5678')}{rng.randint(0, 99):02d} {rng.randint(0, 999):03d} {rng.randint(0, 999):03d}"


def build_email(first_name: str, surname: str, customer_id: str) -> str:
    # The customer number keeps two people with the same name apart.
    customer_number = customer_id.removeprefix(CUSTOMER_ID_FORMAT.format(number=""))
    return f"{to_ascii(first_name)[0]}.{to_ascii(surname)}{customer_number}@{EMAIL_DOMAIN}".lower()


def to_ascii(text: str) -> str:
    # NFKD does not decompose the Polish ł.
    decomposed = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    return "".join(character for character in decomposed if character.isascii())


def list_customer_accounts(customer: CustomerRow, taken_account_numbers: set[str],
                           rng: random.Random) -> list[AccountRow]:
    if customer.customer_id == STORYLINE_CUSTOMER_ID:
        return []
    main_account_type = ACCOUNT_BUSINESS if customer.segment == SEGMENT_BUSINESS else ACCOUNT_CURRENT
    account_types = [main_account_type] + [ACCOUNT_SAVINGS] * (rng.randint(*ACCOUNTS_PER_CUSTOMER) - 1)
    customer_since = date.fromisoformat(customer.customer_since)
    return [AccountRow(build_account_number(taken_account_numbers, rng), customer.customer_id, account_type,
                       build_random_date(customer_since, HISTORY_START_DATE, rng).isoformat())
            for account_type in account_types]


def build_account_number(taken_account_numbers: set[str], rng: random.Random) -> str:
    while True:
        account_number = COUNTRY_CODE + "".join(rng.choice("0123456789") for _ in range(ACCOUNT_NUMBER_DIGITS))
        if account_number not in taken_account_numbers:
            taken_account_numbers.add(account_number)
            return account_number


def list_generated_transactions(accounts: list[AccountRow], customer_by_id: dict[str, CustomerRow],
                                aml_customer_ids: set[str], rng: random.Random) -> list[TransactionRow]:
    unnumbered = []
    for account in accounts:
        customer = customer_by_id[account.customer_id]
        anomaly_rate = AML_CUSTOMER_ANOMALY_RATE if customer.customer_id in aml_customer_ids \
            else REGULAR_CUSTOMER_ANOMALY_RATE
        activity = BUSINESS_ACTIVITY if account.account_type == ACCOUNT_BUSINESS else PERSONAL_ACTIVITY
        for _ in range(rng.randint(*TRANSACTIONS_PER_ACCOUNT)):
            booked_on = build_random_date(HISTORY_START_DATE, AS_OF_DATE, rng)
            anomaly = rng.choice(ANOMALIES) if rng.random() < anomaly_rate else None
            amount = anomaly.amount if anomaly else rng.choice(activity)
            unnumbered.append((booked_on, account, customer.branch, amount, anomaly.pattern if anomaly else None))
    unnumbered.sort(key=lambda row: (row[0], row[1].account_number))
    return [TransactionRow(TRANSACTION_ID_FORMAT.format(number=FIRST_GENERATED_TRANSACTION_NUMBER + index),
                           account.account_number, booked_on.isoformat(), build_amount(amount, rng), amount.direction,
                           amount.channel, amount.counterparty, branch, pattern)
            for index, (booked_on, account, branch, amount, pattern) in enumerate(unnumbered)]


def build_amount(template: AmountTemplate, rng: random.Random) -> float:
    return float(round(rng.uniform(template.minimum_pln, template.maximum_pln), template.rounding_digits))


def block_generated_accounts(accounts: list[AccountRow], aml_customer_ids: set[str],
                             rng: random.Random) -> list[AccountRow]:
    candidates = [account for account in accounts if account not in STORYLINE_ACCOUNTS]
    blocked_numbers = {account.account_number for account in rng.sample(candidates, GENERATED_BLOCKED_ACCOUNT_COUNT)}
    blocked_accounts = []
    for account in accounts:
        if account.account_number not in blocked_numbers:
            blocked_accounts.append(account)
            continue
        reasons = AML_BLOCK_REASONS if account.customer_id in aml_customer_ids else OTHER_BLOCK_REASONS
        blocked_on = build_random_date(HISTORY_START_DATE, AS_OF_DATE - timedelta(days=3), rng).isoformat()
        blocked_accounts.append(AccountRow(account.account_number, account.customer_id, account.account_type,
                                           account.opened_on, blocked_on, rng.choice(reasons)))
    return blocked_accounts


def list_generated_aml_cases(aml_customer_ids: set[str], accounts: list[AccountRow],
                             transactions: list[TransactionRow],
                             rng: random.Random) -> tuple[list[AmlCaseRow], list[AmlCaseTransactionRow]]:
    customer_by_account = {account.account_number: account.customer_id for account in accounts}
    flagged_by_customer: dict[str, list[TransactionRow]] = {customer_id: [] for customer_id in aml_customer_ids}
    for transaction in transactions:
        customer_id = customer_by_account[transaction.account_number]
        if transaction.anomaly_pattern and customer_id in aml_customer_ids \
                and date.fromisoformat(transaction.booked_on) < LATEST_GENERATED_CASE_DATE:
            flagged_by_customer[customer_id].append(transaction)
    pending_cases = sorted((build_pending_aml_case(customer_id, flagged_by_customer[customer_id], rng)
                            for customer_id in sorted(aml_customer_ids)),
                           key=lambda case: (case.opened_on, case.customer_id))
    cases, case_transactions = [], []
    for number, case in enumerate(pending_cases, start=1):
        case_id = AML_CASE_ID_FORMAT.format(number=number)
        cases.append(AmlCaseRow(case_id, case.customer_id, choose_weighted(AML_STATUS_WEIGHTS, rng),
                                choose_weighted(RISK_LEVEL_WEIGHTS, rng), case.opened_on.isoformat()))
        case_transactions.extend(AmlCaseTransactionRow(case_id, transaction_id)
                                 for transaction_id in case.linked_transaction_ids)
    return cases, case_transactions


def build_pending_aml_case(customer_id: str, flagged: list[TransactionRow], rng: random.Random) -> PendingAmlCase:
    # Spreading opening dates over the history makes the case list look like a real backlog, not one busy week.
    opened_on = build_random_date(AS_OF_DATE - timedelta(days=HISTORY_DAYS - EARLIEST_CASE_DAYS_INTO_HISTORY),
                                  LATEST_GENERATED_CASE_DATE, rng)
    linked = [transaction for transaction in flagged if date.fromisoformat(transaction.booked_on) < opened_on]
    if not linked and flagged:
        linked = flagged[:1]
        opened_on = min(date.fromisoformat(linked[0].booked_on) + timedelta(days=rng.randint(
            MIN_DAYS_FROM_FLAG_TO_CASE, MAX_DAYS_FROM_FLAG_TO_CASE)), LATEST_GENERATED_CASE_DATE)
    return PendingAmlCase(customer_id, opened_on,
                          [transaction.transaction_id for transaction in linked[-MAX_LINKED_TRANSACTIONS_PER_CASE:]])


def build_random_date(start: date, end: date, rng: random.Random) -> date:
    return start + timedelta(days=rng.randint(0, max((end - start).days, 0)))


def choose_weighted(weights: dict[str, int], rng: random.Random) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def write_bank_demo_database(db_path: Path, dataset: BankDemoDataset) -> None:
    # Build next to the target and rename, so a crash or a concurrent start never leaves a half-written database.
    temporary_path = db_path.with_name(db_path.name + TEMPORARY_FILE_SUFFIX)
    temporary_path.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary_path)
    try:
        connection.executescript(SCHEMA)
        insert_rows(connection, "customers", dataset.customers)
        insert_rows(connection, "accounts", dataset.accounts)
        insert_rows(connection, "transactions", dataset.transactions)
        insert_rows(connection, "aml_cases", dataset.aml_cases)
        insert_rows(connection, "aml_case_transactions", dataset.aml_case_transactions)
        connection.commit()
    finally:
        connection.close()
    os.replace(temporary_path, db_path)


def insert_rows(connection: sqlite3.Connection, table: str, rows: list) -> None:
    columns = [field.name for field in fields(rows[0])]
    statement = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"
    connection.executemany(statement, [astuple(row) for row in rows])


def create_bank_demo_database(db_path: str | Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    write_bank_demo_database(db_path, build_bank_demo_dataset(random.Random(RANDOM_SEED)))


def ensure_bank_demo_database(db_path: str | Path) -> None:
    if not Path(db_path).exists():
        create_bank_demo_database(db_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the bank demo's SQLite database.")
    parser.add_argument("db_path", type=Path)
    parser.add_argument("--force", action="store_true", help="replace an existing database")
    arguments = parser.parse_args()
    if arguments.db_path.exists() and not arguments.force:
        parser.error(ALREADY_EXISTS_ERROR.format(path=arguments.db_path))
    create_bank_demo_database(arguments.db_path)
    print(CREATED_MESSAGE.format(path=arguments.db_path))


if __name__ == "__main__":
    main()
