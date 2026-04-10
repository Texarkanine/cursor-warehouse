"""cursor-warehouse test configuration and shared fixtures."""

import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "scripts" / "schema.sql"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def db():
    """In-memory DuckDB connection with schema applied."""
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA_PATH.read_text())
    yield con
    con.close()


@pytest.fixture
def db_path(tmp_path):
    """On-disk DuckDB database path with schema applied. Returns (path, connection)."""
    p = tmp_path / "test.duckdb"
    con = duckdb.connect(str(p))
    con.execute(SCHEMA_PATH.read_text())
    yield str(p), con
    con.close()
