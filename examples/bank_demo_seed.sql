-- Fictional bank records served by the demo data MCP server.
-- The executor reads these tables read-only; no demo record is hard-coded in Python.

CREATE TABLE IF NOT EXISTS transaction_anomalies (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    amount_pln REAL NOT NULL,
    branch TEXT NOT NULL,
    pattern TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blocked_accounts (
    account TEXT PRIMARY KEY,
    blocked_on TEXT NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aml_cases (
    case_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    customer_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aml_case_transactions (
    case_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    transaction_id TEXT NOT NULL,
    PRIMARY KEY (case_id, position)
);

CREATE TABLE IF NOT EXISTS customer_contacts (
    customer_id TEXT PRIMARY KEY,
    phone TEXT NOT NULL,
    email TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_workplaces (
    customer_id TEXT PRIMARY KEY,
    employer TEXT NOT NULL,
    city TEXT NOT NULL
);

INSERT OR IGNORE INTO transaction_anomalies (id, date, amount_pln, branch, pattern) VALUES
    ('TX-1001', '2026-09-28', 12500.00, 'Warsaw', 'structuring'),
    ('TX-1003', '2026-09-30', 47000.00, 'Warsaw', 'rapid in-out');

INSERT OR IGNORE INTO blocked_accounts (account, blocked_on, reason) VALUES
    ('PL-****-4411', '2026-09-30', 'suspicious inflows'),
    ('PL-****-9032', '2026-10-01', 'court order');

INSERT OR IGNORE INTO aml_cases (case_id, status, customer_id) VALUES
    ('AML-2026-0042', 'under investigation', 'CUST-17');

INSERT OR IGNORE INTO aml_case_transactions (case_id, position, transaction_id) VALUES
    ('AML-2026-0042', 0, 'TX-1001'),
    ('AML-2026-0042', 1, 'TX-1003');

INSERT OR IGNORE INTO customer_contacts (customer_id, phone, email) VALUES
    ('CUST-17', '+48 601 234 567', 'j.kowalski@example.pl');

INSERT OR IGNORE INTO customer_workplaces (customer_id, employer, city) VALUES
    ('CUST-17', 'Kowalski Logistics sp. z o.o.', 'Warsaw');
