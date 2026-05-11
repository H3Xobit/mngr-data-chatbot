"""Shared test fixtures.

The ``seeded_dbs`` fixture ensures both SQLite databases exist before any
test that needs them runs. Tests that don't need data (e.g. the validator
parametrize set) don't request the fixture and run instantly.
"""

from __future__ import annotations

import pytest

from db import seed as seed_module
from services import schema_service
from utils.config import get_settings


@pytest.fixture(scope="session")
def seeded_dbs():
    s = get_settings()
    # Always re-seed so tests are deterministic regardless of previous runs.
    seed_module.main()
    # Bust the schema cache (it's memoised across the session).
    schema_service.all_tables.cache_clear()
    yield
    # We leave the DB files around - useful for ad-hoc inspection after a run.
    _ = s
