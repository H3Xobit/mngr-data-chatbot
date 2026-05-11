"""Idempotent seed script.

Drops + recreates both SQLite databases, applies the schema DDL, and loads
the provided CSV files into them. Run with::

    python -m db.seed
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

from utils.config import get_settings
from utils.logger import get_logger

log = get_logger(__name__)
SETTINGS = get_settings()

DATA_DIR = SETTINGS.project_root / "data"
SCHEMA_DIR = SETTINGS.project_root / "db" / "schema"


# Maps the CSV file → (table name, ordered column names matching the schema).
# The CSV files happen to ship column-aligned with the schema, so we just
# use the CSV header verbatim - but we go through this map explicitly so a
# future column rename in the CSVs doesn't silently corrupt the DB.
ECOMMERCE_LOAD = [
    ("ecom_categories.csv", "categories", ["id", "name", "description"]),
    ("ecom_customers.csv", "customers", ["id", "name", "email", "location"]),
    (
        "ecom_products.csv",
        "products",
        ["id", "name", "description", "price", "category_id"],
    ),
    (
        "ecom_orders.csv",
        "orders",
        ["id", "customer_id", "order_date", "total_amount"],
    ),
]

SUPPORT_LOAD = [
    ("support_agents.csv", "agents", ["id", "name", "department", "expertise"]),
    (
        "support_customers.csv",
        "customers",
        ["id", "name", "email", "contact_info", "account_status"],
    ),
    (
        "support_tickets.csv",
        "tickets",
        ["id", "title", "description", "customer_id", "status", "priority"],
    ),
    (
        "support_interactions.csv",
        "interactions",
        ["id", "ticket_id", "agent_id", "timestamp", "notes"],
    ),
]


def _apply_schema(conn: sqlite3.Connection, ddl_path: Path) -> None:
    sql = ddl_path.read_text(encoding="utf-8")
    conn.executescript(sql)


def _load_csv(
    conn: sqlite3.Connection,
    csv_path: Path,
    table: str,
    columns: list[str],
) -> int:
    placeholders = ",".join("?" * len(columns))
    col_list = ",".join(columns)
    insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

    rows: list[tuple] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in columns if c not in (reader.fieldnames or [])]
        if missing:
            raise RuntimeError(
                f"{csv_path.name} is missing expected columns: {missing}. "
                f"Found: {reader.fieldnames}"
            )
        for row in reader:
            rows.append(tuple(row[c] if row[c] != "" else None for c in columns))

    conn.executemany(insert_sql, rows)
    return len(rows)


def _seed_database(db_path: Path, ddl_path: Path, csv_dir: Path, manifest) -> None:
    if db_path.exists():
        db_path.unlink()
    log.info("Creating %s", db_path.name)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _apply_schema(conn, ddl_path)
        for csv_name, table, cols in manifest:
            n = _load_csv(conn, csv_dir / csv_name, table, cols)
            log.info("  %-25s → %s rows", csv_name, n)
        conn.commit()


def main() -> int:
    log.info("Seeding databases. project_root=%s", SETTINGS.project_root)

    if not DATA_DIR.exists():
        log.error("Missing data directory: %s", DATA_DIR)
        return 1

    _seed_database(
        SETTINGS.ecommerce_db_path,
        SCHEMA_DIR / "ecommerce.sql",
        DATA_DIR / "ecommerce",
        ECOMMERCE_LOAD,
    )
    _seed_database(
        SETTINGS.support_db_path,
        SCHEMA_DIR / "support.sql",
        DATA_DIR / "support",
        SUPPORT_LOAD,
    )

    log.info("Seed complete.")
    log.info("  e-commerce DB: %s", SETTINGS.ecommerce_db_path)
    log.info("  support    DB: %s", SETTINGS.support_db_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
