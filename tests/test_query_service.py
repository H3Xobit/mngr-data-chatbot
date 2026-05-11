"""Tests for the SQL validator and read-only executor.

These hit the real seeded SQLite databases via the read-only connection.
They assume `python -m db.seed` has been run (conftest does it on demand).
"""

from __future__ import annotations

import pytest

from services import query_service
from services.query_service import UnsafeQueryError


# ---------- Validator -------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select 1",
        "  SELECT id FROM ecommerce.customers",
        "WITH c AS (SELECT 1) SELECT * FROM c",
        "-- a comment\nSELECT 1",
        "SELECT 1;",  # trailing semicolon OK
    ],
)
def test_validator_accepts_selects(sql):
    assert query_service.validate_select(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM ecommerce.customers",
        "DROP TABLE ecommerce.customers",
        "UPDATE ecommerce.customers SET name = 'x'",
        "INSERT INTO ecommerce.customers(id, name, email) VALUES (99, 'x', 'x@x')",
        "ALTER TABLE ecommerce.customers ADD COLUMN x TEXT",
        "PRAGMA foreign_keys = OFF",
        "ATTACH DATABASE 'evil.db' AS evil",
        "DETACH DATABASE support",
        "VACUUM",
        "REINDEX",
        "",
        "   ",
        "SELECT 1; DROP TABLE ecommerce.customers",  # statement stacking
        "SELECT 1; SELECT 2",
    ],
)
def test_validator_rejects_unsafe(sql):
    with pytest.raises(UnsafeQueryError):
        query_service.validate_select(sql)


# ---------- Execution against the real seeded databases --------------------


def test_run_query_counts_customers(seeded_dbs):
    result = query_service.run_query("SELECT COUNT(*) AS n FROM ecommerce.customers")
    assert result.row_count == 1
    assert result.rows[0]["n"] == 13


def test_run_query_counts_tickets(seeded_dbs):
    result = query_service.run_query("SELECT COUNT(*) AS n FROM support.tickets")
    assert result.row_count == 1
    assert result.rows[0]["n"] == 15


def test_cross_domain_join_by_email(seeded_dbs):
    """Customers in BOTH domains."""
    sql = """
        SELECT e.name
        FROM   ecommerce.customers AS e
        JOIN   support.customers   AS s ON s.email = e.email
        ORDER  BY e.name
    """
    result = query_service.run_query(sql)
    names = [r["name"] for r in result.rows]
    assert "Alice Chen" in names
    assert "Ben Okafor" in names
    # All names returned must appear in BOTH tables, so count must be
    # <= min(13, 12).
    assert result.row_count <= 12


def test_purchasers_without_tickets(seeded_dbs):
    """Example query #4 from the brief - implemented as SQL."""
    sql = """
        SELECT e.email
        FROM   ecommerce.customers e
        WHERE  e.id IN (SELECT DISTINCT customer_id FROM ecommerce.orders)
        AND    NOT EXISTS (
            SELECT 1
            FROM   support.customers s
            JOIN   support.tickets t ON t.customer_id = s.id
            WHERE  s.email = e.email
        )
    """
    result = query_service.run_query(sql)
    # At least one such customer should exist in the sample data.
    assert result.row_count >= 1


def test_total_order_value_for_ticket_raisers(seeded_dbs):
    """Example query #3 from the brief."""
    sql = """
        SELECT e.email,
               COALESCE(SUM(o.total_amount), 0) AS total_orders
        FROM   ecommerce.customers e
        JOIN   ecommerce.orders     o ON o.customer_id = e.id
        WHERE  e.email IN (
            SELECT s.email
            FROM   support.customers s
            JOIN   support.tickets   t ON t.customer_id = s.id
        )
        GROUP  BY e.email
        ORDER  BY total_orders DESC
    """
    result = query_service.run_query(sql)
    assert result.row_count >= 1
    assert "email" in result.columns
    assert "total_orders" in result.columns
    # Every total must be a positive number.
    assert all(r["total_orders"] > 0 for r in result.rows)


def test_run_query_rejects_unsafe(seeded_dbs):
    with pytest.raises(UnsafeQueryError):
        query_service.run_query("DELETE FROM ecommerce.customers")


def test_run_query_truncates(seeded_dbs):
    result = query_service.run_query("SELECT * FROM ecommerce.orders", max_rows=3)
    assert result.row_count == 3
    assert result.truncated is True


def test_invalid_sql_surfaces_friendly_error(seeded_dbs):
    with pytest.raises(UnsafeQueryError) as excinfo:
        query_service.run_query("SELECT * FROM ecommerce.nope")
    assert "SQL error" in str(excinfo.value)
