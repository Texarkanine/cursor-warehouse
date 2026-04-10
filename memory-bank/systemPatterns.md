# System Patterns

## How This System Works

cursor-warehouse is a direct port of claude-warehouse. It consists of standalone Python scripts (no package installation required) that run via `uv run --script`, each declaring their own dependencies via PEP 723 inline metadata. The central data store is a DuckDB database file at `~/.cursor/cursor-warehouse.duckdb`.

Data flows: Cursor JSONL transcripts → `sync.py` → DuckDB → query/dashboard/embed/vsearch scripts. A supplementary SQLite database (`ai-code-tracking.db`) provides model info and commit-level AI attribution.

## PEP 723 Script Metadata

Every Python script in `scripts/` has an inline `# /// script` block declaring its Python version and dependencies. This enables `uv run --script <file>` to create an isolated environment per-script without a global `pyproject.toml`. This is inherited from the upstream architecture and is deliberate — packaging is VISION2 scope.

## Harness Column

All provenance tables (`sessions`, `messages`, `tool_calls`, `embeddings`, `scored_commits`) include a `harness TEXT NOT NULL DEFAULT 'cursor'` column. This enables future multi-harness support (e.g., adding Claude Code data alongside Cursor data in the same warehouse).

## Watermark-Based Incremental Sync

The `_sync_state` table tracks file mtimes per source category. On each sync run, only files with mtimes newer than the stored watermark are processed. Dedup within a session is handled by delete-and-reinsert.
