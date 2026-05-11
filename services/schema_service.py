"""Introspect both attached databases and present schemas to the LLM.

Result is cached at process start - schemas don't change at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from services.db_service import DOMAIN_ALIASES, readonly_connection
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str
    notnull: bool
    pk: bool


@dataclass(frozen=True)
class TableInfo:
    domain: str          # 'ecommerce' or 'support'
    name: str
    columns: tuple[ColumnInfo, ...]
    row_count: int


def _list_tables(conn, domain: str) -> list[str]:
    rows = conn.execute(
        f"SELECT name FROM {domain}.sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def _columns(conn, domain: str, table: str) -> tuple[ColumnInfo, ...]:
    rows = conn.execute(f"PRAGMA {domain}.table_info({table})").fetchall()
    return tuple(
        ColumnInfo(
            name=r["name"], type=r["type"], notnull=bool(r["notnull"]), pk=bool(r["pk"])
        )
        for r in rows
    )


def _row_count(conn, domain: str, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) AS n FROM {domain}.{table}").fetchone()["n"]


@lru_cache(maxsize=1)
def all_tables() -> tuple[TableInfo, ...]:
    """Return every table across both domains."""
    out: list[TableInfo] = []
    with readonly_connection() as conn:
        for domain in DOMAIN_ALIASES:
            for tbl in _list_tables(conn, domain):
                out.append(
                    TableInfo(
                        domain=domain,
                        name=tbl,
                        columns=_columns(conn, domain, tbl),
                        row_count=_row_count(conn, domain, tbl),
                    )
                )
    return tuple(out)


def schema_summary_for_prompt() -> str:
    """Human-readable schema block we inject into the LLM system prompt."""
    lines: list[str] = []
    by_domain: dict[str, list[TableInfo]] = {}
    for t in all_tables():
        by_domain.setdefault(t.domain, []).append(t)

    for domain, tables in by_domain.items():
        lines.append(f"--- {domain} database (alias: `{domain}`) ---")
        for t in tables:
            cols = ", ".join(
                f"{c.name} {c.type}{' PRIMARY KEY' if c.pk else ''}"
                for c in t.columns
            )
            lines.append(f"  {domain}.{t.name}({cols})  [{t.row_count} rows]")
        lines.append("")
    lines.append(
        "Cross-domain join key: `ecommerce.customers.email == support.customers.email` "
        "(same person across both domains)."
    )
    return "\n".join(lines)
