-- Customer Support domain schema.
-- Matches the columns of the provided support_*.csv files exactly.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS agents (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    department TEXT    NOT NULL,
    expertise  TEXT
);

CREATE TABLE IF NOT EXISTS customers (
    id             INTEGER PRIMARY KEY,
    name           TEXT    NOT NULL,
    email          TEXT    NOT NULL UNIQUE,   -- cross-domain join key
    contact_info   TEXT,
    account_status TEXT    NOT NULL           -- active / suspended / etc.
);

CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY,
    title       TEXT    NOT NULL,
    description TEXT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    status      TEXT    NOT NULL,             -- open / closed / pending / etc.
    priority    TEXT    NOT NULL              -- high / medium / low
);

CREATE TABLE IF NOT EXISTS interactions (
    id        INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    agent_id  INTEGER NOT NULL REFERENCES agents(id),
    timestamp TEXT    NOT NULL,               -- ISO yyyy-mm-dd hh:mm:ss
    notes     TEXT
);

CREATE INDEX IF NOT EXISTS idx_tickets_customer       ON tickets(customer_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status         ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_priority       ON tickets(priority);
CREATE INDEX IF NOT EXISTS idx_interactions_ticket    ON interactions(ticket_id);
CREATE INDEX IF NOT EXISTS idx_interactions_agent     ON interactions(agent_id);
CREATE INDEX IF NOT EXISTS idx_customers_email        ON customers(email);
