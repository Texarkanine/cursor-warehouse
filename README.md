# cursor-warehouse

Developer analytics for AI-assisted coding in Cursor. Syncs your agent session data into a DuckDB warehouse for querying, visualization, and semantic search.

> **Direct port of [claude-warehouse](https://github.com/sderosiaux/claude-warehouse)** (MIT) — adapted to read Cursor agent transcripts instead of Claude Code data. Full credit to the upstream project.

## What it does

- **Syncs** Cursor agent transcripts (`~/.cursor/projects/*/agent-transcripts/**/*.jsonl`) into a local DuckDB database
- **Enriches** session data with model info and AI attribution from `ai-code-tracking.db`
- **Queries** sessions, messages, tool calls, and scored commits via CLI
- **Visualizes** usage patterns on a local dashboard (port 3141)
- **Searches** past sessions with keyword and semantic (vector) search
- **Attributes** AI vs human code contribution at the commit level

## Data sources

| Source | Path | What it provides |
|--------|------|------------------|
| Agent transcripts | `~/.cursor/projects/*/agent-transcripts/**/*.jsonl` | Sessions, messages, tool calls |
| AI tracking DB | `~/.cursor/ai-tracking/ai-code-tracking.db` | Model per request, scored commits (AI %) |

## Quick start

Requires [uv](https://docs.astral.sh/uv/) (Python package runner).

```bash
# Sync your Cursor sessions into DuckDB
uv run --script scripts/sync.py -v

# Query recent sessions
uv run --script scripts/query.py sessions

# Search across all sessions
uv run --script scripts/query.py search "authentication"

# Start the dashboard
uv run --script scripts/dashboard.py
# Open http://127.0.0.1:3141

# Generate embeddings for semantic search
uv run --script scripts/embed.py -v

# Semantic search
uv run --script scripts/vsearch.py "how to handle JWT tokens"
```

## Schema

All provenance tables include a `harness` column (defaults to `'cursor'`) for future multi-harness support.

| Table | Description |
|-------|-------------|
| `sessions` | One row per agent session |
| `messages` | Individual turns from JSONL transcripts |
| `tool_calls` | Extracted tool invocations (Read, Write, Shell, etc.) |
| `scored_commits` | Commit-level AI attribution (tab/composer/human lines) |
| `embeddings` | Vector embeddings for semantic search |
| `_sync_state` | Watermarks for incremental sync |

## Skills

Install as a Cursor plugin to get agent skills:

| Skill | Description |
|-------|-------------|
| `query` | Raw SQL against the warehouse |
| `recall` | Cross-session memory search |
| `report` | Analytics report on development habits |
| `wrapped` | Fun stats summary (Spotify Wrapped style) |

## Hooks

For automatic sync on session start, copy `hooks/hooks.json` to `.cursor/hooks.json` in your workspace, or install as a Cursor plugin.

## Running tests

```bash
uv run --with pytest --with duckdb pytest tests/ -v
```

## License

MIT — see [LICENSE](LICENSE).

## Credits

This project is a direct port of [claude-warehouse](https://github.com/sderosiaux/claude-warehouse) by Stéphane Derosiaux. The upstream architecture, PEP 723 script pattern, DuckDB warehouse design, and dashboard are all inherited. Cursor-specific adaptations include the JSONL parser rewrite, `ai-code-tracking.db` integration, `scored_commits` table, and tool name remapping.
