"""Adversarial SQL tests.

For every category of attack we want to defend against, there is a test
here. The validator is whitelist-first (only SELECT/WITH), but layered
defences mean even if the validator missed something, the connection
itself (PRAGMA query_only) and the disabled load_extension would catch it.

These tests should ALL pass without anything reaching real data writes.
"""

from __future__ import annotations

import sqlite3

import pytest

from services import query_service
from services.db_service import readonly_connection
from services.query_service import UnsafeQueryError, run_query, validate_select

# ---- Validator-level rejections ----------------------------------------------

ATTACK_VECTORS = [
    # Simple destructive statements.
    ("drop table",          "DROP TABLE ecommerce.customers"),
    ("delete all",          "DELETE FROM support.tickets"),
    ("update statement",    "UPDATE ecommerce.customers SET email='x' WHERE 1"),
    ("insert statement",    "INSERT INTO ecommerce.orders VALUES (99,1,'now',0)"),
    ("replace statement",   "REPLACE INTO ecommerce.orders VALUES (99,1,'now',0)"),
    ("truncate",            "TRUNCATE support.tickets"),
    # Schema mutations.
    ("create table",        "CREATE TABLE evil (x INT)"),
    ("alter",               "ALTER TABLE ecommerce.customers ADD COLUMN x INT"),
    # SQLite-specific mischief.
    ("attach",              "ATTACH DATABASE '/tmp/evil.db' AS evil"),
    ("detach",              "DETACH DATABASE ecommerce"),
    ("pragma writable",     "PRAGMA writable_schema = ON"),
    ("vacuum",              "VACUUM"),
    ("reindex",             "REINDEX"),
    # Statement stacking.
    ("stacked drop after select", "SELECT 1; DROP TABLE ecommerce.customers"),
    ("stacked delete after select", "SELECT * FROM support.tickets; DELETE FROM support.tickets"),
    # WITH at top level but containing a non-SELECT.
    ("with non-select",     "WITH t AS (SELECT 1) DELETE FROM ecommerce.customers"),
    # The classic comment-hiding trick.
    ("comment-hidden attack", "SELECT 1 -- ; DROP TABLE ecommerce.customers\n; SELECT 2"),
    # Function-based escapes.
    ("load_extension",      "SELECT load_extension('/tmp/evil.so')"),
    # Empty / whitespace.
    ("empty",               ""),
    ("only whitespace",     "   \t\n  "),
    ("only semicolon",      ";"),
]


@pytest.mark.parametrize("name,sql", ATTACK_VECTORS)
def test_validator_blocks_attack(name, sql):
    """Every attack vector above must raise UnsafeQueryError."""
    with pytest.raises(UnsafeQueryError):
        validate_select(sql)


def test_load_extension_is_disabled_at_connection_level(seeded_dbs):
    """Even if the validator missed it, sqlite3.enable_load_extension is OFF.

    This is the Python sqlite3 default but we want a regression test in case
    somebody flips it on.
    """
    with readonly_connection() as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("SELECT load_extension('/tmp/anything.so')")


def test_pragma_query_only_blocks_writes_even_if_validator_misses(seeded_dbs):
    """Belt-and-braces: connection refuses writes regardless of validator."""
    with readonly_connection() as conn:
        # ATTACH and DETACH at the connection level are also disabled by query_only.
        # Try an INSERT directly via the connection (bypassing the validator).
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO ecommerce.customers (id, name, email, location) "
                "VALUES (999, 'evil', 'evil@x', 'X')"
            )


def test_pragma_query_only_blocks_delete_even_via_connection(seeded_dbs):
    with readonly_connection() as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM ecommerce.customers WHERE 1")


def test_legal_with_select_passes(seeded_dbs):
    """A real CTE-style query (WITH ... SELECT) must work end-to-end."""
    result = run_query(
        "WITH recent AS (SELECT id, order_date FROM ecommerce.orders) "
        "SELECT COUNT(*) AS n FROM recent"
    )
    assert result.row_count == 1
    assert result.rows[0]["n"] >= 0


def test_validator_normalises_trailing_semicolon():
    """A single trailing ';' is fine and must not be misread as stacking."""
    assert validate_select("SELECT 1;").rstrip(";") == "SELECT 1"


def test_validator_strips_lead_whitespace_and_comments():
    """SQL prefixed with a comment or whitespace should still validate."""
    assert validate_select("-- a comment\nSELECT 1") == "-- a comment\nSELECT 1"
    assert validate_select(
        "/* multi\nline\ncomment */\nSELECT 2"
    ).strip().endswith("SELECT 2")


def test_running_a_blocked_query_via_chat_dispatcher_returns_structured_error():
    """The chat-service tool dispatcher must surface unsafe-query errors
    structurally, not crash."""
    from services import chat_service

    result, echo = chat_service._execute_tool_call(
        "execute_sql", {"query": "DROP TABLE ecommerce.customers"}
    )
    assert echo is None
    assert result["error"] == "invalid_sql"
    assert "hint" in result, "LLM needs a hint on how to recover"


def test_query_service_module_exposes_the_two_error_classes():
    """Public API contract: callers can `except QueryTooExpensiveError` and
    `except UnsafeQueryError` separately."""
    assert hasattr(query_service, "UnsafeQueryError")
    assert hasattr(query_service, "QueryTooExpensiveError")
    # They must be different exception types so callers can discriminate.
    assert query_service.UnsafeQueryError is not query_service.QueryTooExpensiveError
