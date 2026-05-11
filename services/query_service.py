"""SQL validation + execution.

We let the LLM emit arbitrary SELECT statements but apply three layers of
safety before they run against the real data:

1. **Syntactic whitelist.** The query must be a single statement, must
   start with ``SELECT`` (or ``WITH ... SELECT``), and must not contain
   any forbidden keyword (``INSERT``, ``UPDATE``, ``DELETE``, ``ATTACH``,
   ``DETACH``, ``PRAGMA``, ``CREATE``, ``DROP``, ``REPLACE``, ``ALTER``).
2. **No semicolons inside the query** (other than an optional trailing one)
   to defeat statement stacking.
3. **Connection-level read-only.** The connection itself is opened against
   an in-memory main database with the real DBs attached read-only and
   ``PRAGMA query_only = ON``. Even if the validator missed something,
   SQLite refuses to write.

We then run the statement with a hard row cap, format the result as a list
of dicts, and bubble a friendly error message up to the LLM if anything
went wrong.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from services.db_service import readonly_connection
from utils.logger import get_logger

log = get_logger(__name__)


class UnsafeQueryError(ValueError):
    """The supplied SQL violated a safety rule."""


FORBIDDEN = (
    "INSERT", "UPDATE", "DELETE", "REPLACE",
    "DROP", "CREATE", "ALTER", "TRUNCATE",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX",
)

# Allow leading whitespace, comments, then SELECT or WITH.
_SELECT_RE = re.compile(
    r"^\s*(--[^\n]*\n|/\*.*?\*/|\s)*(SELECT|WITH)\b",
    re.IGNORECASE | re.DOTALL,
)


def validate_select(sql: str) -> str:
    """Return the validated SQL (trimmed) or raise UnsafeQueryError."""
    if not sql or not sql.strip():
        raise UnsafeQueryError("Empty query.")
    cleaned = sql.strip().rstrip(";").strip()

    # Statement stacking via semicolons inside the body is rejected.
    if ";" in cleaned:
        raise UnsafeQueryError(
            "Multiple statements are not allowed; submit a single SELECT."
        )

    if not _SELECT_RE.match(cleaned):
        raise UnsafeQueryError("Only SELECT (or WITH ... SELECT) statements are allowed.")

    # Token-aware forbidden-keyword check (matches whole words, not substrings).
    upper = cleaned.upper()
    for kw in FORBIDDEN:
        if re.search(rf"\b{kw}\b", upper):
            raise UnsafeQueryError(f"Disallowed keyword in query: {kw}")

    return cleaned


# ---------- Execution -------------------------------------------------------


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool
    row_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
        }


DEFAULT_MAX_ROWS = 200


def run_query(sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> QueryResult:
    """Validate + execute SQL against the read-only connection."""
    safe_sql = validate_select(sql)
    log.info("Executing SQL (cap=%d rows): %s", max_rows, safe_sql)

    with readonly_connection() as conn:
        try:
            cur = conn.execute(safe_sql)
        except sqlite3.Error as exc:
            log.warning("SQL error: %s", exc)
            raise UnsafeQueryError(f"SQL error: {exc}") from exc

        cols = [d[0] for d in cur.description] if cur.description else []
        rows: list[dict[str, Any]] = []
        truncated = False
        for i, row in enumerate(cur):
            if i >= max_rows:
                truncated = True
                break
            rows.append({k: row[k] for k in cols})

    return QueryResult(
        columns=cols, rows=rows, truncated=truncated, row_count=len(rows)
    )
