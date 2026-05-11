"""Tests for the chat service's tool dispatcher.

We exercise the tool-execution boundary directly so we don't have to mock
Groq for the basics.
"""

from __future__ import annotations

from services import chat_service


def test_execute_sql_tool_returns_rows(seeded_dbs):
    result, echo = chat_service._execute_tool_call(
        "execute_sql",
        {"query": "SELECT id, name FROM ecommerce.customers ORDER BY id LIMIT 2"},
    )
    assert echo is not None
    assert echo.row_count == 2
    assert echo.sql.strip().startswith("SELECT")
    assert "rows" in result
    assert len(result["rows"]) == 2
    assert {"id", "name"} <= set(result["columns"])


def test_execute_sql_tool_rejects_writes(seeded_dbs):
    result, echo = chat_service._execute_tool_call(
        "execute_sql",
        {"query": "DELETE FROM ecommerce.customers"},
    )
    assert echo is None
    assert result["error"] == "invalid_sql"
    assert "SELECT" in result["hint"]


def test_execute_sql_tool_rejects_statement_stacking(seeded_dbs):
    result, _ = chat_service._execute_tool_call(
        "execute_sql",
        {"query": "SELECT 1; DROP TABLE ecommerce.customers"},
    )
    assert result["error"] == "invalid_sql"


def test_execute_sql_tool_handles_invalid_table(seeded_dbs):
    result, _ = chat_service._execute_tool_call(
        "execute_sql",
        {"query": "SELECT * FROM ecommerce.does_not_exist"},
    )
    assert result["error"] == "invalid_sql"
    assert "SQL error" in result["message"]


def test_execute_sql_tool_empty_query(seeded_dbs):
    result, _ = chat_service._execute_tool_call("execute_sql", {})
    assert result["error"] == "missing_query"


def test_describe_schema_tool(seeded_dbs):
    result, echo = chat_service._execute_tool_call("describe_schema", {})
    assert echo is None
    assert "ecommerce.customers" in result["schema"]
    assert "support.tickets" in result["schema"]


def test_unknown_tool(seeded_dbs):
    result, _ = chat_service._execute_tool_call("send_email", {})
    assert result["error"] == "unknown_tool"
