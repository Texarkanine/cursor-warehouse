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

**First-time setup:** After installing the plugin, run the **`cw:initialize`** skill (or follow [`skills/cw-initialize/SKILL.md`](skills/cw-initialize/SKILL.md)). It checks prerequisites, optionally configures user-level **`uv`** for PyTorch (with your permission), runs a **one-time full sync** and **one-time embed**, and can start the dashboard. That order avoids “empty warehouse” surprises and GPU/`torch` footguns.

Manual equivalents (if you already know what you’re doing):

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

# Generate embeddings for semantic search (full message text + stripped user_query per message)
uv run --script scripts/embed.py -v

# Semantic search (default: full messages + sessions; add -t message_user_query for user-intent-only vectors)
uv run --script scripts/vsearch.py "how to handle JWT tokens"
```

### Embeddings and PyTorch (`embed.py`)

`embed.py` pulls **`torch`** (and **`sentence-transformers`**) via PEP 723. PyTorch’s CUDA wheels are published on **`download.pytorch.org`**, not only on PyPI, so **`uv`** needs an extra index for a typical **Linux + NVIDIA** setup.

**User-level config** (recommended; no `pyproject.toml` required):

| OS | Path |
|----|------|
| Linux / macOS | `~/.config/uv/uv.toml` |
| Windows | `%APPDATA%\uv\uv.toml` |

Example — **CUDA 12.6** wheels (adjust `cu126` to match [PyTorch’s install matrix](https://pytorch.org/get-started/locally/) and your driver):

```toml
[[index]]
name = "pytorch-cu126"
url = "https://download.pytorch.org/whl/cu126"
```

PyPI remains the default index for everything else (`duckdb`, `sentence-transformers`, …). **Apple Silicon** and **CPU-only** users should use the **macOS** or **CPU** line from the same matrix instead of a `cu*` URL.

**Optional smoke test** (prints `torch` version and whether CUDA is available):

```bash
uv run --script scripts/uv_torch_smoke.py
```

If **`uv`** fails while installing NVIDIA-related wheels with **`The wheel is invalid`** / **`Metadata field Name not found`**, run **`uv cache clean`** and **`uv self update`**, then retry (stale cache is a common cause).

## Schema

All provenance tables include a `harness` column (defaults to `'cursor'`) for future multi-harness support.

| Table | Description |
|-------|-------------|
| `sessions` | One row per agent session |
| `messages` | Individual turns from JSONL transcripts |
| `tool_calls` | Extracted tool invocations (Read, Write, Shell, etc.) |
| `scored_commits` | Commit-level AI attribution (tab/composer/human lines) |
| `embeddings` | Vectors for semantic search (`message`, `message_user_query`, `session`) |
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
| `cw:initialize` | First-time setup — run after install to populate the warehouse |
| `cw:query` | Raw SQL against the warehouse |
| `cw:recall` | Cross-session memory search |
| `cw:report` | Analytics report on development habits |
| `cw:wrapped` | Fun stats summary (Spotify Wrapped style) |

## Hooks

The plugin launches a thin `hook-launcher.py` on session start that forks `sync.py` and `dashboard.py` as fully detached processes (they survive Cursor quitting). For manual hook setup without the plugin, copy `hooks/hooks.json` to `.cursor/hooks.json` in your workspace.

## Running tests

```bash
uv run --with pytest --with duckdb pytest tests/ -v
```

## License

MIT — see [LICENSE](LICENSE).

## Credits

This project is a direct port of [claude-warehouse](https://github.com/sderosiaux/claude-warehouse) by Stéphane Derosiaux. The upstream architecture, PEP 723 script pattern, DuckDB warehouse design, and dashboard are all inherited. Cursor-specific adaptations include the JSONL parser rewrite, `ai-code-tracking.db` integration, `scored_commits` table, and tool name remapping.
