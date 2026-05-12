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


class QueryTooExpensiveError(ValueError):
    """The query plan looks like a runaway (cartesian / full scan over a big table).

    Distinct from ``UnsafeQueryError`` because the SQL is *legal*, just expensive.
    Surface this separately so the LLM can be told 'narrow your query' rather
    than 'that's forbidden'.
    """


FORBIDDEN = (
    "INSERT", "UPDATE", "DELETE", "REPLACE",
    "DROP", "CREATE", "ALTER", "TRUNCATE",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX",
)

# Function-name calls we never want the LLM to invoke. ``load_extension`` would
# let attacker-supplied .so files run if Python's sqlite3 had it enabled
# (it doesn't by default, but defence-in-depth: reject at the validator too).
FORBIDDEN_FUNCTIONS = ("load_extension",)

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

    # Reject dangerous function calls (case-insensitive).
    lower = cleaned.lower()
    for fn in FORBIDDEN_FUNCTIONS:
        if re.search(rf"\b{re.escape(fn)}\s*\(", lower):
            raise UnsafeQueryError(f"Disallowed function call: {fn}")

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

# Cost-guard thresholds.
# A SCAN over more rows than this is considered too expensive to run blindly.
DEFAULT_MAX_SCAN_ROWS = 50_000
# More than this many tables (without restricting JOINs) is treated as a
# cartesian product risk.
MAX_TABLES_WITHOUT_JOIN = 2


def _table_row_count(conn: sqlite3.Connection, qualified_table: str) -> int:
    """Best-effort row count for a (schema.table) reference, swallowing errors.

    We use this only inside the cost guard so a temporary FROM-clause oddity
    can't break a valid query - it can only cause us to skip the guard.
    """
    try:
        cur = conn.execute(f"SELECT COUNT(*) FROM {qualified_table}")
        n = cur.fetchone()
        return int(n[0]) if n else 0
    except sqlite3.Error:
        return 0


def _explain_plan_violations(
    conn: sqlite3.Connection, sql: str, max_scan_rows: int
) -> list[str]:
    """Return human-readable violations of the cost guard, or [] if the plan is fine.

    Heuristic, deliberately conservative:
    - A "SCAN" step on a table containing more than ``max_scan_rows`` rows is
      flagged. The seeded demo tables are tiny so the guard never fires on
      real-world example queries.
    - A query naming more than ``MAX_TABLES_WITHOUT_JOIN`` distinct tables
      without any "USING INDEX" or "SEARCH ... USING" hint suggests a likely
      cartesian product.
    """
    try:
        plan_rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
    except sqlite3.Error:
        # If EXPLAIN itself fails the query will fail too; let the real
        # execution surface a precise error rather than guessing here.
        return []

    violations: list[str] = []
    tables_seen: set[str] = set()
    has_join_restriction = False

    for row in plan_rows:
        # SQLite's EXPLAIN QUERY PLAN returns (id, parent, notused, detail)
        detail = row["detail"] if isinstance(row, sqlite3.Row) else row[-1]
        if not detail:
            continue
        d = str(detail)

        # Pull out the table name when the plan mentions one.
        m = re.search(r"\b(?:SCAN|SEARCH)\s+(?:TABLE\s+)?([A-Za-z0-9_\.]+)", d)
        if m:
            tables_seen.add(m.group(1))

        if "USING INDEX" in d or "USING COVERING INDEX" in d or "SEARCH" in d:
            has_join_restriction = True

        if d.startswith("SCAN") and m:
            row_count = _table_row_count(conn, m.group(1))
            if row_count > max_scan_rows:
                violations.append(
                    f"Plan SCANs {m.group(1)} (~{row_count:,} rows); "
                    f"add a WHERE filter or an index. "
                    f"(Cap is {max_scan_rows:,}.)"
                )

    if len(tables_seen) > MAX_TABLES_WITHOUT_JOIN and not has_join_restriction:
        violations.append(
            f"Query references {len(tables_seen)} tables ({sorted(tables_seen)!r}) "
            "without an indexed JOIN/SEARCH step. That looks like a cartesian "
            "product. Add an explicit JOIN condition."
        )

    return violations


def run_query(
    sql: str,
    max_rows: int = DEFAULT_MAX_ROWS,
    *,
    enforce_cost_guard: bool = True,
    max_scan_rows: int = DEFAULT_MAX_SCAN_ROWS,
) -> QueryResult:
    """Validate + execute SQL against the read-only connection.

    Steps:
        1. Syntactic validation (``validate_select``).
        2. EXPLAIN QUERY PLAN cost guard (off only for internal/test use).
        3. Run with a hard row cap.
    """
    safe_sql = validate_select(sql)
    log.info("Executing SQL (cap=%d rows): %s", max_rows, safe_sql)

    with readonly_connection() as conn:
        if enforce_cost_guard:
            violations = _explain_plan_violations(conn, safe_sql, max_scan_rows)
            if violations:
                msg = "; ".join(violations)
                log.warning("Cost-guard rejected query: %s", msg)
                raise QueryTooExpensiveError(msg)
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
