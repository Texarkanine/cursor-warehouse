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

## Platform support

Tested on **Windows 11 + WSL** (Cursor launched from a WSL terminal). The scripts run inside WSL and automatically find Cursor data on both the WSL and Windows sides.

Native Windows and macOS are expected to work (all paths use `~/.cursor/`) but have not been tested. See [issue tracking](https://github.com/Texarkanine/cursor-warehouse/issues) for platform-specific reports.

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

## Installation

### From GitHub (main branch)

In Cursor, go to **Settings > Plugins**, paste the repo URL into "Search or Paste Link":

```
https://github.com/Texarkanine/cursor-warehouse
```

Cursor installs from the default branch. Skills and hooks activate automatically.

### Local / branch testing

Cursor's GitHub installer only pulls the default branch. To test an unmerged branch locally, symlink the repo into Cursor's local plugin directory and register it via the shared config surface:

```bash
# 1. Symlink your working tree
ln -sfn /path/to/cursor-warehouse ~/.cursor/plugins/local/cursor-warehouse

# 2. Register the plugin (upsert into existing file — don't clobber other entries)
python3 -c "
import json, pathlib, sys
p = pathlib.Path.home() / '.claude/plugins/installed_plugins.json'
data = json.loads(p.read_text()) if p.exists() else {'version': 2, 'plugins': {}}
data['plugins']['cursor-warehouse@local'] = [{'scope': 'user', 'installPath': str(pathlib.Path.home() / '.cursor/plugins/local/cursor-warehouse')}]
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(data, indent=2))
"

# 3. Enable the plugin
python3 -c "
import json, pathlib
p = pathlib.Path.home() / '.claude/settings.json'
data = json.loads(p.read_text()) if p.exists() else {}
data.setdefault('enabledPlugins', {})['cursor-warehouse@local'] = True
p.write_text(json.dumps(data, indent=2))
"

# 4. Restart Cursor
```

To uninstall the local override, remove the entries from both JSON files, delete the symlink, and restart Cursor.

## Skills

| Skill | Description |
|-------|-------------|
| `query` | Raw SQL against the warehouse |
| `recall` | Cross-session memory search |
| `report` | Analytics report on development habits |
| `wrapped` | Fun stats summary (Spotify Wrapped style) |

## Hooks

The plugin runs `sync.py` and starts the dashboard automatically on session start. For manual hook setup without the plugin, copy `hooks/hooks.json` to `.cursor/hooks.json` in your workspace.

## Running tests

```bash
uv run --with pytest --with duckdb pytest tests/ -v
```

## License

MIT — see [LICENSE](LICENSE).

## Credits

This project is a direct port of [claude-warehouse](https://github.com/sderosiaux/claude-warehouse) by Stéphane Derosiaux. The upstream architecture, PEP 723 script pattern, DuckDB warehouse design, and dashboard are all inherited. Cursor-specific adaptations include the JSONL parser rewrite, `ai-code-tracking.db` integration, `scored_commits` table, and tool name remapping.
