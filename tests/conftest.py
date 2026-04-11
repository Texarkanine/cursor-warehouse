"""cursor-warehouse test configuration and shared fixtures."""

import sys
from pathlib import Path

import duckdb
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from schema_util import ensure_sync_state_last_path  # noqa: E402

SCHEMA_PATH = SCRIPTS_DIR / "schema.sql"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def db():
    """In-memory DuckDB connection with schema applied."""
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA_PATH.read_text())
    ensure_sync_state_last_path(con)
    yield con
    con.close()


@pytest.fixture
def db_path(tmp_path):
    """On-disk DuckDB database path with schema applied. Returns (path, connection)."""
    p = tmp_path / "test.duckdb"
    con = duckdb.connect(str(p))
    con.execute(SCHEMA_PATH.read_text())
    ensure_sync_state_last_path(con)
    yield str(p), con
    con.close()
