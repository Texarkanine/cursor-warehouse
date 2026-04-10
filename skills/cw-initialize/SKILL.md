---
name: "cw:initialize"
description: First-time setup for cursor-warehouse. Run this after installing the plugin to populate the DuckDB warehouse with your Cursor agent session history. Handles initial sync, optional embedding generation, and dashboard startup.
---

# Initialize — First-Time Setup

Run this skill after installing cursor-warehouse for the first time. It performs the initial data import, which takes 10-30+ seconds depending on your session history size.

## Finding the scripts

`CURSOR_PLUGIN_ROOT` should be set when invoked through the plugin system, but may be unset during development. Resolve once per session:

Use `find -L` so a symlinked dev install (e.g. `~/.cursor/plugins/local/cursor-warehouse` → your clone) is traversed.

```bash
PLUGIN_SCRIPTS="${CURSOR_PLUGIN_ROOT:+$CURSOR_PLUGIN_ROOT/scripts}"
if [ -z "$PLUGIN_SCRIPTS" ] || [ ! -d "$PLUGIN_SCRIPTS" ]; then
  PLUGIN_SCRIPTS="$(dirname "$(find -L ~/.cursor/plugins -name sync.py -path '*/cursor-warehouse/*/sync.py' 2>/dev/null | head -1)")"
fi
```

## Step 1: Full Sync

Run the full sync to import all Cursor agent transcripts and tracking data:

```bash
uv run --script "$PLUGIN_SCRIPTS/sync.py" --full --verbose
```

This will:
- Discover all JSONL files under `~/.cursor/projects/*/agent-transcripts/`
- Parse sessions, messages, and tool calls into DuckDB
- Import model info and scored commits from `ai-code-tracking.db` (if available)

Report the results to the user (sessions, messages, tool calls, scored commits imported).

## Step 2: Semantic Embeddings (Optional)

If the user wants semantic search capabilities (via `cw:recall`), generate embeddings:

```bash
uv run --script "$PLUGIN_SCRIPTS/embed.py" --verbose
```

This requires `sentence-transformers` and `torch` (downloaded automatically by `uv`). First run downloads the model (~90MB). Embedding generation takes 1-5 minutes depending on message volume.

Ask the user before running — it's optional and requires significant download/compute.

## Step 3: Dashboard (Optional)

Start the analytics dashboard:

```bash
uv run --script "$PLUGIN_SCRIPTS/dashboard.py" &
```

The dashboard serves on `http://127.0.0.1:3141`. Future sessions will auto-start the dashboard via the sessionStart hook.

## Step 4: Confirm

Report that the warehouse is ready. Mention the available skills:
- `cw:query` — Run raw SQL against the warehouse
- `cw:recall` — Search across past sessions (keyword + semantic)
- `cw:report` — Generate an analytics report
- `cw:wrapped` — Fun stats summary (like Spotify Wrapped)
