"""SQLite connection management.

We open an empty in-memory ``main`` database and ATTACH both real
databases read-only under explicit aliases ``ecommerce`` and ``support``.
The LLM therefore writes unambiguous queries like::

    SELECT e.name, COUNT(t.id) AS tickets
    FROM   ecommerce.customers AS e
    JOIN   support.customers   AS s ON s.email = e.email
    JOIN   support.tickets     AS t ON t.customer_id = s.id
    GROUP BY e.id;

The main schema is empty and ``PRAGMA query_only = ON`` is set, so
even a bypassed SELECT validator can't mutate anything.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from utils.config import get_settings
from utils.logger import get_logger

DOMAIN_ALIASES = ("ecommerce", "support")

log = get_logger(__name__)


@contextmanager
def readonly_connection() -> Iterator[sqlite3.Connection]:
    """Yield a read-only connection with ecommerce + support attached.

    Read-only is enforced two ways:
    - the main DB is a throw-away ``:memory:`` with nothing in it, and
    - ``PRAGMA query_only = ON`` is set on the connection after attach.
    The query validator additionally rejects any non-SELECT statement.
    """
    s = get_settings()
    if not s.ecommerce_db_path.exists() or not s.support_db_path.exists():
        raise FileNotFoundError(
            "Database files not found. Run `python -m db.seed` first to "
            "create ecommerce.db and support.db from the CSV fixtures."
        )

    conn = sqlite3.connect(":memory:", timeout=2.0, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "ATTACH DATABASE ? AS ecommerce", (str(s.ecommerce_db_path),)
        )
        conn.execute(
            "ATTACH DATABASE ? AS support", (str(s.support_db_path),)
        )
        conn.execute("PRAGMA query_only = ON")
        yield conn
    finally:
        conn.close()
