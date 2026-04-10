# VISIONA: cursor-warehouse

> Direct port of [claude-warehouse](https://github.com/sderosiaux/claude-warehouse) for Cursor. Copy the code, change the data source, ship it.

## What This Is

A Cursor plugin that does exactly what claude-warehouse does for Claude Code: syncs your Cursor agent session data into a DuckDB warehouse for querying, visualization, and semantic search. Published to the Cursor marketplace.

This is a **direct fork**, not a clean-room reimplementation. We copy claude-warehouse's source (MIT licensed), modify it to read Cursor data instead of Claude Code data, add a `harness` column for future multi-harness support, and publish.

## Scope

### Copy

Copy every file from [claude-warehouse](https://github.com/sderosiaux/claude-warehouse) into this repo:

```
scripts/sync.py       → scripts/sync.py
scripts/schema.sql    → scripts/schema.sql
scripts/embed.py      → scripts/embed.py
scripts/vsearch.py    → scripts/vsearch.py
scripts/query.py      → scripts/query.py
scripts/dashboard.py  → scripts/dashboard.py
static/index.html     → static/index.html
skills/               → skills/
```

Preserve the PEP 723 script pattern. Preserve the `uv run --script` invocation style. We are deliberately keeping the upstream architecture for this project — the supply chain hardening and proper packaging is future work (VISION2.md).

### Change: Schema

**`scripts/schema.sql`** — Add `harness TEXT NOT NULL DEFAULT 'cursor'` to:

- `sessions`
- `messages`
- `tool_calls`
- `embeddings`

Drop tables that have no Cursor equivalent:

- `deleted_sessions` — depends on Claude Code's `sessions-index.json`
- `hook_events` — depends on Claude Code's `~/.claude/logs/` format
- `todos` — depends on `~/.claude/todos/`
- `debug_logs` — depends on `~/.claude/debug/`
- `research_history` — depends on `~/.claude/history/`

Keep:

- `_sync_state` — watermark system is source-agnostic
- `sessions` — portable with column adjustments
- `messages` — portable with column adjustments
- `tool_calls` — portable as-is
- `embeddings` — fully portable

### Change: Session Discovery & JSONL Parsing

This is the main rewrite. **`scripts/sync.py`** needs to read Cursor data instead of Claude Code data.

**Discovery path:**

```
~/.claude/projects/**/*.jsonl
```
becomes:
```
~/.cursor/projects/*/agent-transcripts/**/*.jsonl
```

Cursor transcript layout:
```
~/.cursor/projects/<workspace-slug>/agent-transcripts/
├── <parent-uuid>/
│   ├── <parent-uuid>.jsonl          # Main transcript
│   └── subagents/
│       ├── <subagent-uuid>.jsonl    # Subagent transcript
│       └── ...
└── <another-uuid>/
    └── ...
```

The workspace slug encodes the project path (e.g., `home-mobaxterm-Documents-git-myproject`).

**JSONL format differences:**

| Field | Claude Code | Cursor |
|-------|-------------|--------|
| Top-level type | `type` ("user", "assistant") | `role` ("user", "assistant") |
| Message UUID | `uuid` (in each record) | **None** — generate from line index or content hash |
| Parent UUID | `parentUuid` | **None** — linear (no tree structure) |
| Session ID | `sessionId` (in records) | Directory name (UUID from path) |
| Timestamp | `timestamp` (ISO 8601 per message) | **None per message** — derive from file mtime or directory metadata |
| Content | `message.content[]` | `message.content[]` (same structure) |
| Content block types | `text`, `tool_use`, `tool_result` | `text`, `tool_use`, `tool_result` (same) |
| Token usage | `message.usage.{input,output,cache_read,cache_creation}_tokens` | **None** — Cursor doesn't expose per-message token counts |
| Model | `message.model` | **None in JSONL** — may be discoverable from metadata |
| Sidechain | `isSidechain` | **None** |
| CWD/version/branch | In first record | **None** — workspace slug encodes project path |
| Subagent detection | Path contains `/subagents/` | Path contains `/subagents/` (same convention!) |

**Key implementation notes for the JSONL parser rewrite:**

1. **Session ID** = the parent UUID directory name
2. **Message UUIDs** = generate as `{session_id}:{line_number}` (stable, deterministic)
3. **Timestamps** = not available per-message. Use file mtime for `created_at`/`modified_at` on the session. Individual message timestamps can be NULL.
4. **Token counts** = not available. Leave as 0 or NULL. Cost calculations in the dashboard won't work — that's fine, we'll show "N/A" instead of fake numbers.
5. **Content blocks** = the `message.content[]` array structure is identical. `text` and `tool_use` blocks parse the same way.
6. **Subagents** = same `/subagents/` directory convention. Same detection logic.
7. **Project name** = derived from workspace slug (decode hyphens back to path separators, take the last segment).

**Sync sources to remove** (Claude-specific, no Cursor equivalent):

- Deleted sessions sync (`sessions-index.json` parsing)
- Hook events sync (`~/.claude/logs/`)
- Todos sync (`~/.claude/todos/`)
- Debug logs sync (`~/.claude/debug/`)
- Research history sync (`~/.claude/history/`)
- Purge logic (JSONL file cleanup)

**Keep:**

- Watermark system (`_sync_state`, `newer_files()`, `set_watermark()`) — works as-is
- Dedup logic (delete-and-reinsert per session) — works as-is

### Change: DB Path

```python
# Claude Code
CLAUDE_DIR = Path.home() / ".claude"
DB_PATH = CLAUDE_DIR / "claude.duckdb"

# Cursor
CURSOR_DIR = Path.home() / ".cursor"
DB_PATH = CURSOR_DIR / "cursor-warehouse.duckdb"
```

### Change: CLI (`scripts/query.py`)

- Rename prog from `cw` to `cursor-warehouse` (or `csw` for brevity)
- Update DB_PATH default
- `tokens` command: remove cost calculations (no token data from Cursor), or show token counts as "N/A"
- `tools` command: works as-is (reads from `tool_calls` table)
- `sessions` command: works as-is
- `search` command: works as-is
- `projects` command: works as-is
- `size` command: works as-is
- `hooks` command: **remove** (no hook_events table)
- `vsearch` command: update path reference
- `sql` command: works as-is

### Change: Dashboard (`scripts/dashboard.py`)

- Update DB_PATH
- `/api/overview`: remove cost calculation, or show "N/A"
- `/api/costs`: **remove or repurpose** — no token cost data. Could repurpose as session count by project.
- `/api/tools`: works as-is (reads from tool_calls)
- `/api/sessions`: works as-is
- `/api/trends`: remap tool name categories:
  - Write tools: `Write`, `StrReplace`, `EditNotebook` (Cursor) vs `Edit`, `MultiEdit`, `Write` (Claude Code)
  - Read tools: `Read`, `Glob`, `Grep`, `SemanticSearch` (Cursor) vs `Read`, `Grep`, `Glob` (Claude Code)
- `/api/efficiency`: session length buckets work as-is; prompt length vs cost doesn't (no cost data)
- `/api/wrapped`: remap tool-to-personality mapping for Cursor tool names
- Update `static/index.html` title/branding from "claude-warehouse" to "cursor-warehouse"

### Change: Embeddings (`scripts/embed.py`)

- Update DB_PATH
- Everything else works as-is — embeddings are computed from `messages.text_content` and `sessions.first_prompt`, which are populated by the sync regardless of data source

### Change: Plugin Packaging

**Remove:** `.claude-plugin/plugin.json`

**Add:** `.cursor-plugin/plugin.json`

```json
{
  "name": "cursor-warehouse",
  "description": "Developer analytics for AI-assisted coding in Cursor. Sessions captured, searchable, queryable.",
  "version": "0.1.0",
  "author": {
    "name": "<your-name>"
  },
  "homepage": "https://github.com/<your-repo>/cursor-warehouse",
  "repository": "https://github.com/<your-repo>/cursor-warehouse",
  "license": "MIT",
  "keywords": ["analytics", "memory", "duckdb", "cursor", "search", "dashboard"]
}
```

### Change: Hooks

**`hooks/hooks.json`** — same pattern, different env var:

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": ".*",
      "hooks": [{
        "type": "command",
        "command": "${CURSOR_PLUGIN_ROOT}/scripts/dashboard.py &",
        "timeout": 5000
      }]
    }]
  }
}
```

Also add a sync hook — the upstream only has a dashboard hook, but we should sync on SessionStart too:

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": ".*",
      "hooks": [
        {
          "type": "command",
          "command": "uv run --script ${CURSOR_PLUGIN_ROOT}/scripts/sync.py",
          "timeout": 30000
        },
        {
          "type": "command",
          "command": "${CURSOR_PLUGIN_ROOT}/scripts/dashboard.py &",
          "timeout": 5000
        }
      ]
    }]
  }
}
```

### Change: Skills

Port upstream skills, replacing `claude-warehouse` references with `cursor-warehouse`:

- `skills/query/SKILL.md` — tell agent how to use `cursor-warehouse query`
- `skills/recall/SKILL.md` — semantic search for past sessions
- `skills/costs/SKILL.md` — **remove or stub** (no cost data)
- `skills/report/SKILL.md` — session analytics
- `skills/wrapped/SKILL.md` — "Spotify Wrapped" for your dev sessions

### Change: Hardcoded References

Global search-and-replace across all files:

| Find | Replace |
|------|---------|
| `claude-warehouse` | `cursor-warehouse` |
| `claude_warehouse` | `cursor_warehouse` |
| `~/.claude` (data paths) | `~/.cursor` |
| `CLAUDE_DIR` | `CURSOR_DIR` |
| `CLAUDE_PLUGIN_ROOT` | `CURSOR_PLUGIN_ROOT` |
| `claude.duckdb` | `cursor-warehouse.duckdb` |
| `cw` (CLI prog name) | `cursor-warehouse` |

### Don't Change

- PEP 723 script headers (keep `uv run --script` pattern)
- DuckDB as the warehouse engine
- sentence-transformers + torch for embeddings
- Chart.js dashboard
- HTTP server on port 3141
- HNSW index for vector similarity

## What This Does NOT Include

- Proper Python packaging (pyproject.toml, uv.lock) — that's VISION2
- Supply chain hardening — that's VISION2
- Multi-harness support (Claude Code adapter) — that's VISION2
- Adapter interface / Protocol — that's VISION2
- Global CLI wrapper mechanism — that's VISION2
- Release automation (release-please for version bumps, tags, changelogs) — that's VISION2

## Acceptance Criteria

1. `uv run --script scripts/sync.py` reads Cursor agent transcripts and populates DuckDB
2. `uv run --script scripts/query.py sessions` shows Cursor sessions
3. `uv run --script scripts/query.py search "something"` finds messages
4. `uv run --script scripts/embed.py` generates embeddings from Cursor session data
5. `uv run --script scripts/dashboard.py` serves the dashboard with Cursor data
6. Plugin installs from the Cursor marketplace and works on SessionStart
7. `harness` column exists on all provenance-sensitive tables (defaulting to `'cursor'`)

## Estimated Complexity

**L3** — the JSONL parser rewrite is the main work. Everything else is search-and-replace plus dropping Claude-specific features. Multiple components touched but the architecture is inherited, not designed.

## Future: Token Counts via `state.vscdb`

The VISIONA port assumes token counts are unavailable because the JSONL transcripts don't include them. This is only half-true. Cursor stores rich per-message metadata — including token counts — in a separate set of SQLite databases that the JSONL files don't expose.

### Data Source

Cursor's "composer" UI state lives in two SQLite databases using a key-value schema (`cursorDiskKV` table):

| Database | Location | Contains |
|---|---|---|
| Global storage | `~/.config/Cursor/User/globalStorage/state.vscdb` | Individual message "bubbles" (content + metadata) |
| Workspace storage | `~/.config/Cursor/User/workspaceStorage/<workspace_id>/state.vscdb` | Session-level "composerData" (metadata + message ordering) |

These are standard SQLite files despite the `.vscdb` extension.

### Key-Value Layout

```sql
-- Session metadata (workspace DB)
SELECT value FROM cursorDiskKV WHERE key = 'composerData:<composerId>';
-- Returns JSON with: composerId, name, createdAt, lastUpdatedAt, fullConversationHeadersOnly

-- Individual message (global DB)
SELECT value FROM cursorDiskKV WHERE key = 'bubbleId:<composerId>:<bubbleId>';
-- Returns JSON with 100+ fields per bubble
```

### Token-Relevant Fields per Bubble

Each bubble JSON blob contains:

```json
{
  "tokenCount": {
    "inputTokens": 12345,
    "outputTokens": 6789
  },
  "thinkingDurationMs": 4200,
  "usageUuid": "...",
  "isRefunded": false
}
```

- `tokenCount.inputTokens` / `tokenCount.outputTokens` — the per-message token counts we currently show as 0.
- `thinkingDurationMs` — how long the model spent in "thinking" mode (useful for extended-thinking model analytics).
- `isRefunded` — whether the request was refunded (useful for filtering out failed/retried requests).

### Additional Rich Metadata Available

Beyond tokens, each bubble includes fields that could enhance analytics:

| Field | Value for cursor-warehouse |
|---|---|
| `thinkingDurationMs` | Model thinking time — enables thinking-time-vs-quality analysis |
| `isAgentic` | Whether agent mode was used (vs. inline/composer chat) |
| `unifiedMode` | Numeric mode indicator (correlates with chat mode) |
| `approximateLintErrors` | Lint errors in context at message time |
| `gitDiffs` | Git diff state at message time |
| `attachedCodeChunks` | Code context attached to the message |
| `toolFormerData` | Richer tool call data (includes `rawArgs` and `result`) |
| `useWeb` | Whether web search was used |

### Integration Strategy

To get token counts into cursor-warehouse, add `state.vscdb` as a third sync source alongside JSONL transcripts and `ai-code-tracking.db`:

1. **Discover workspace DBs**: Glob `~/.config/Cursor/User/workspaceStorage/*/state.vscdb`
2. **For each workspace DB**: Read `composerData` entries to get session IDs (= `composerId`) and session names
3. **Cross-reference**: Match `composerId` values to existing sessions by UUID (the composer ID should match or map to the agent-transcript directory UUID)
4. **Enrich from global DB**: For matched sessions, read bubble data from the global `state.vscdb` to pull `tokenCount`, `thinkingDurationMs`, and other metadata
5. **Backfill**: UPDATE existing `messages` rows with token counts; UPDATE `sessions` with aggregated totals and session names

The matching step (3) needs investigation — it's not yet confirmed whether `composerId` values in `state.vscdb` correspond 1:1 with the agent-transcript UUIDs. If they don't match directly, the `usageUuid` or `serverBubbleId` fields on bubbles may provide an alternate join path.

### Caveats

- The `state.vscdb` databases are Cursor's live UI state. They may be write-locked while Cursor is running — use `PRAGMA journal_mode=wal` or open read-only with `?mode=ro`.
- Bubble data is ephemeral — Cursor may prune old entries. The JSONL transcripts remain the authoritative long-term record; `state.vscdb` is a supplementary enrichment source.
- The `state.vscdb` schema is undocumented and internal to Cursor. It may change without notice across Cursor versions.
- This approach was discovered by cross-referencing with [cursor-chronicle](https://github.com/mikhailsal/cursor-chronicle), which reads exclusively from `state.vscdb` but does not use `ai-code-tracking.db` or agent transcripts.

## Future: Release Automation

Cursor's marketplace tracks the **default branch** and re-indexes on push via GitHub webhooks. It does not support tag-based refs or pinning to a specific version. The `version` field in `plugin.json` is the marketplace's version — consumers always get whatever's on main.

For versioning discipline without fighting the marketplace model, use [release-please](https://github.com/googleapis/release-please) on main:
- Automates version bumps in `plugin.json` (via a custom `release-please-config.json` targeting `.cursor-plugin/plugin.json`)
- Creates git tags and GitHub Releases for changelog/audit purposes
- The marketplace picks up whatever lands on main regardless — tags are for our own record

This is a VISION2 concern. For VISIONA, `plugin.json` version is manually set to `0.1.0`.

## Attribution

- MIT license from upstream preserved on all copied files
- README credits claude-warehouse as the upstream source
- Courtesy issue opened on the upstream repo
