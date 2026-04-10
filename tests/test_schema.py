"""Tests for cursor-warehouse DuckDB schema."""

import duckdb
import pytest


EXPECTED_TABLES = {"_sync_state", "sessions", "messages", "tool_calls", "embeddings", "scored_commits"}
DROPPED_TABLES = {"deleted_sessions", "hook_events", "todos", "debug_logs", "research_history"}

HARNESS_TABLES = {"sessions", "messages", "tool_calls", "embeddings", "scored_commits"}

SCORED_COMMITS_COLUMNS = {
    "commit_hash", "branch_name", "harness", "scored_at",
    "lines_added", "lines_deleted",
    "tab_lines_added", "tab_lines_deleted",
    "composer_lines_added", "composer_lines_deleted",
    "human_lines_added", "human_lines_deleted",
    "blank_lines_added", "blank_lines_deleted",
    "commit_message", "commit_date",
    "v1_ai_percentage", "v2_ai_percentage",
}


def _get_tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()
    return {r[0] for r in rows}


def _get_columns(con: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    rows = con.execute(
        "SELECT column_name, column_default FROM information_schema.columns "
        "WHERE table_schema = 'main' AND table_name = ?",
        [table],
    ).fetchall()
    return {r[0]: r[1] for r in rows}


class TestSchemaCreation:
    def test_schema_executes_without_error(self, db):
        result = db.execute("SELECT 1").fetchone()
        assert result == (1,)

    def test_expected_tables_exist(self, db):
        tables = _get_tables(db)
        for t in EXPECTED_TABLES:
            assert t in tables, f"Expected table '{t}' not found"

    def test_dropped_tables_do_not_exist(self, db):
        tables = _get_tables(db)
        for t in DROPPED_TABLES:
            assert t not in tables, f"Dropped table '{t}' should not exist"


class TestHarnessColumn:
    def test_harness_column_exists_on_provenance_tables(self, db):
        for table in HARNESS_TABLES:
            cols = _get_columns(db, table)
            assert "harness" in cols, f"Table '{table}' missing 'harness' column"

    def test_harness_default_is_cursor(self, db):
        for table in HARNESS_TABLES:
            cols = _get_columns(db, table)
            default = cols["harness"]
            assert default is not None and "cursor" in default.lower(), (
                f"Table '{table}' harness default should be 'cursor', got: {default}"
            )


class TestScoredCommitsSchema:
    def test_scored_commits_has_correct_columns(self, db):
        cols = _get_columns(db, "scored_commits")
        col_names = set(cols.keys())
        assert col_names == SCORED_COMMITS_COLUMNS, (
            f"scored_commits column mismatch.\n"
            f"  Missing: {SCORED_COMMITS_COLUMNS - col_names}\n"
            f"  Extra: {col_names - SCORED_COMMITS_COLUMNS}"
        )

    def test_scored_commits_primary_key(self, db):
        db.execute(
            "INSERT INTO scored_commits (commit_hash, branch_name) VALUES ('abc123', 'main')"
        )
        with pytest.raises(duckdb.ConstraintException):
            db.execute(
                "INSERT INTO scored_commits (commit_hash, branch_name) VALUES ('abc123', 'main')"
            )

    def test_scored_commits_different_branches_allowed(self, db):
        db.execute(
            "INSERT INTO scored_commits (commit_hash, branch_name) VALUES ('abc123', 'main')"
        )
        db.execute(
            "INSERT INTO scored_commits (commit_hash, branch_name) VALUES ('abc123', 'feature')"
        )
        count = db.execute("SELECT COUNT(*) FROM scored_commits").fetchone()[0]
        assert count == 2


class TestSchemaIdempotency:
    def test_schema_can_be_applied_twice(self, db):
        """Schema uses IF NOT EXISTS so re-application should be safe."""
        from conftest import SCHEMA_PATH
        db.execute(SCHEMA_PATH.read_text())
        tables = _get_tables(db)
        for t in EXPECTED_TABLES:
            assert t in tables
