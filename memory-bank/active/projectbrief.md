# Project Brief: cursor-warehouse (VISIONA)

## Summary

Direct port of [claude-warehouse](https://github.com/sderosiaux/claude-warehouse) (MIT-licensed) into cursor-warehouse. Copy the upstream source, modify it to read Cursor agent transcript data instead of Claude Code data, add a `harness` column for future multi-harness support, and publish as a Cursor plugin.

## Requirements

Full scope defined in `memory-bank/VISIONA.md`. Key deliverables:

1. Copy all upstream files preserving PEP 723 / `uv run --script` pattern
2. Schema: add `harness TEXT NOT NULL DEFAULT 'cursor'` to provenance tables; drop Claude-specific tables
3. JSONL parser rewrite (`sync.py`): Cursor discovery path, format differences (no per-message UUIDs/timestamps/tokens)
4. Adapt scripts (query.py, dashboard.py, embed.py): update paths, remove/stub cost features, remap tool names
5. Plugin packaging: `.cursor-plugin/plugin.json`
6. Hooks: SessionStart for sync + dashboard
7. Skills: port with cursor-warehouse references
8. Global renames: claude → cursor throughout

## Out of Scope

- Proper Python packaging (pyproject.toml, uv.lock) — VISION2
- Supply chain hardening — VISION2
- Multi-harness support / adapter interface — VISION2

## Acceptance Criteria

1. `uv run --script scripts/sync.py` reads Cursor agent transcripts and populates DuckDB
2. `uv run --script scripts/query.py sessions` shows Cursor sessions
3. `uv run --script scripts/query.py search "something"` finds messages
4. `uv run --script scripts/embed.py` generates embeddings from Cursor session data
5. `uv run --script scripts/dashboard.py` serves the dashboard with Cursor data
6. Plugin installs from the Cursor marketplace and works on SessionStart
7. `harness` column exists on all provenance-sensitive tables (defaulting to `'cursor'`)

## Constraints

- MIT license preserved from upstream on all copied files
- README credits claude-warehouse as upstream source
