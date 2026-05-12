"""Tests for the EXPLAIN QUERY PLAN cost guard.

The seeded demo tables are small, so the real example queries never trip
the guard. These tests therefore use a fresh in-memory DB whose tables we
fill with enough rows to make a full SCAN look expensive.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from services import query_service
from services.query_service import (
    DEFAULT_MAX_SCAN_ROWS,
    QueryTooExpensiveError,
    _explain_plan_violations,
    run_query,
)


@pytest.fixture
def big_conn():
    """In-memory DB with a 'wide' table that has more rows than the scan cap."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE wide (id INTEGER, name TEXT)")
    # Fill with enough rows that a SCAN would trip the default cap.
    rows = ((i, f"name-{i}") for i in range(DEFAULT_MAX_SCAN_ROWS + 100))
    conn.executemany("INSERT INTO wide (id, name) VALUES (?, ?)", rows)
    yield conn
    conn.close()


def test_explain_violations_flags_full_scan_on_big_table(big_conn):
    """A `SELECT *` from a 50k+ row table without WHERE is flagged."""
    violations = _explain_plan_violations(
        big_conn, "SELECT * FROM wide", max_scan_rows=DEFAULT_MAX_SCAN_ROWS
    )
    assert any("SCAN" in v for v in violations)
    assert any("WHERE" in v for v in violations), (
        "should suggest adding a WHERE filter"
    )


def test_explain_violations_passes_indexed_lookup(big_conn):
    """SCAN with a tiny LIMIT or WHERE on rowid still fires (we don't special-case),
    but a query that doesn't SCAN at all should pass cleanly."""
    big_conn.execute("CREATE INDEX wide_id_idx ON wide(id)")
    violations = _explain_plan_violations(
        big_conn, "SELECT * FROM wide WHERE id = 42", max_scan_rows=DEFAULT_MAX_SCAN_ROWS
    )
    assert violations == []


def test_explain_violations_flags_cartesian_product():
    """Two tables with no JOIN/USING constraint should be flagged."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE a (id INT)")
    conn.execute("CREATE TABLE b (id INT)")
    conn.execute("CREATE TABLE c (id INT)")
    # Three tables, no JOIN -> cartesian product.
    violations = _explain_plan_violations(
        conn,
        "SELECT a.id, b.id, c.id FROM a, b, c",
        max_scan_rows=DEFAULT_MAX_SCAN_ROWS,
    )
    assert any("cartesian" in v.lower() for v in violations)


def test_run_query_raises_on_cost_violation(seeded_dbs):
    """run_query should raise QueryTooExpensiveError when the guard triggers."""
    # Pretend the seeded tables are huge so the guard fires.
    # We patch the row-counter to return a number above the cap.
    with patch.object(query_service, "_table_row_count", return_value=999_999):
        with pytest.raises(QueryTooExpensiveError):
            run_query("SELECT * FROM ecommerce.customers")


def test_run_query_cost_guard_can_be_disabled(seeded_dbs):
    """For internal callers (tests, admin scripts) the guard can be skipped."""
    with patch.object(query_service, "_table_row_count", return_value=999_999):
        # Should not raise when guard is off.
        result = run_query(
            "SELECT * FROM ecommerce.customers",
            enforce_cost_guard=False,
        )
    assert result.row_count > 0


def test_cost_guard_does_not_block_legitimate_brief_queries(seeded_dbs):
    """The 4 example queries from the brief must NOT trip the cost guard.

    Regression test: any future tweak to the guard heuristics must keep
    the canonical demo queries flowing.
    """
    queries = [
        # Q1
        """SELECT ecommerce.orders.id FROM ecommerce.orders
           JOIN ecommerce.customers ON ecommerce.orders.customer_id = ecommerce.customers.id
           WHERE ecommerce.customers.name = 'Alice Chen'""",
        # Q2
        """SELECT support.tickets.id FROM support.tickets
           JOIN support.customers ON support.tickets.customer_id = support.customers.id
           WHERE support.customers.name = 'Ben Okafor' AND support.tickets.status = 'open'""",
        # Q3 - cross-domain join
        """SELECT ecommerce.customers.name, SUM(ecommerce.orders.total_amount)
           FROM ecommerce.customers
           JOIN ecommerce.orders ON ecommerce.customers.id = ecommerce.orders.customer_id
           JOIN support.customers ON ecommerce.customers.email = support.customers.email
           JOIN support.tickets ON support.customers.id = support.tickets.customer_id
           GROUP BY ecommerce.customers.name""",
        # Q4 - left-anti-join
        """SELECT ecommerce.customers.name FROM ecommerce.customers
           WHERE ecommerce.customers.email NOT IN (SELECT email FROM support.customers)
           AND ecommerce.customers.id IN (SELECT customer_id FROM ecommerce.orders)""",
    ]
    for sql in queries:
        # Should not raise.
        result = run_query(sql)
        assert isinstance(result.row_count, int)
