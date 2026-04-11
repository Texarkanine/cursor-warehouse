"""Shared DuckDB schema helpers for sync.py and embed.py."""

import duckdb


def ensure_sync_state_last_path(con: duckdb.DuckDBPyConnection) -> None:
    """Add last_path to _sync_state when upgrading older DBs (mtime-only watermarks)."""
    row = con.execute(
        "SELECT 1 FROM pragma_table_info('_sync_state') WHERE name = 'last_path'"
    ).fetchone()
    if row is None:
        con.execute(
            "ALTER TABLE _sync_state ADD COLUMN last_path VARCHAR DEFAULT ''"
        )
