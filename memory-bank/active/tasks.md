# Task: cursor-warehouse VISIONA Port

* Task ID: visiona-port
* Complexity: Level 3
* Type: feature (direct fork/port)

Port [claude-warehouse](https://github.com/sderosiaux/claude-warehouse) (MIT) to read Cursor agent transcript data instead of Claude Code data. Copy upstream source, modify JSONL parser, adapt scripts, add `harness` column, and package as a Cursor plugin.

## Pinned Info

### Data Flow

Architecture overview — how Cursor transcript data flows through the system.

```mermaid
graph LR
    classDef source fill:#e1f5fe,stroke:#01579b;
    classDef script fill:#f3e5f5,stroke:#7b1fa2;
    classDef store fill:#fff3e0,stroke:#ef6c00;
    classDef ui fill:#e8f5e9,stroke:#2e7d32;

    T["~/.cursor/projects/*/agent-transcripts/**/*.jsonl"]:::source
    ACT["~/.cursor/ai-tracking/ai-code-tracking.db"]:::source
    S["sync.py"]:::script
    DB["cursor-warehouse.duckdb"]:::store
    E["embed.py"]:::script
    Q["query.py"]:::script
    D["dashboard.py"]:::script
    V["vsearch.py"]:::script
    H["static/index.html"]:::ui

    T -->|"parse JSONL"| S
    ACT -->|"model, timestamps, scored_commits"| S
    S -->|"sessions, messages, tool_calls, scored_commits"| DB
    DB --> Q
    DB --> D
    DB --> E
    E -->|"embeddings table"| DB
    DB --> V
    D -->|"serves"| H
```

### Modified Schema (ER)

Cursor-warehouse retains 4 of the upstream's 9 data tables plus the watermark table. All provenance tables gain a `harness` column. Additionally, `scored_commits` is a new Cursor-specific table sourced from `ai-code-tracking.db`.

```mermaid
erDiagram
    _sync_state {
        VARCHAR source_name PK
        DOUBLE last_mtime
        TIMESTAMP last_run
        INTEGER files_synced
        BIGINT rows_synced
    }
    sessions {
        VARCHAR session_id PK
        TEXT harness "NOT NULL DEFAULT cursor"
        VARCHAR project_path
        VARCHAR project_name
        TIMESTAMP created_at
        TIMESTAMP modified_at
        INTEGER message_count
        JSON tools_used
        JSON models_used
        VARCHAR first_prompt
        VARCHAR file_path
        BOOLEAN is_subagent
        VARCHAR parent_session_id
    }
    messages {
        VARCHAR session_id PK
        VARCHAR uuid PK
        TEXT harness "NOT NULL DEFAULT cursor"
        VARCHAR type
        TIMESTAMP timestamp
        VARCHAR role
        VARCHAR model
        JSON content_types
        VARCHAR tool_name
        VARCHAR text_content
    }
    tool_calls {
        VARCHAR session_id PK
        VARCHAR message_uuid PK
        INTEGER idx PK
        TEXT harness "NOT NULL DEFAULT cursor"
        VARCHAR tool_name
        VARCHAR tool_input
        TIMESTAMP timestamp
    }
    embeddings {
        VARCHAR source_type PK
        VARCHAR source_id PK
        INTEGER chunk_idx PK
        TEXT harness "NOT NULL DEFAULT cursor"
        VARCHAR text_preview
        FLOAT_384 embedding
    }
    scored_commits {
        VARCHAR commit_hash PK
        VARCHAR branch_name PK
        TEXT harness "NOT NULL DEFAULT cursor"
        TIMESTAMP scored_at
        INTEGER lines_added
        INTEGER lines_deleted
        INTEGER tab_lines_added
        INTEGER tab_lines_deleted
        INTEGER composer_lines_added
        INTEGER composer_lines_deleted
        INTEGER human_lines_added
        INTEGER human_lines_deleted
        INTEGER blank_lines_added
        INTEGER blank_lines_deleted
        VARCHAR commit_message
        TIMESTAMP commit_date
        VARCHAR v1_ai_percentage
        VARCHAR v2_ai_percentage
    }

    sessions ||--o{ messages : "session_id"
    sessions ||--o{ tool_calls : "session_id"
    messages ||--o{ tool_calls : "uuid = message_uuid"
```

### Cursor Data Sources

Cursor stores agent data across two sources (verified by on-disk analysis of 218 JSONL files and the tracking SQLite database):

**Primary: `agent-transcripts/` JSONL** — all current GUI agent sessions (parent + subagent)
- Path: `~/.cursor/projects/{workspace-slug}/agent-transcripts/{session-uuid}/{session-uuid}.jsonl`
- 165 parent sessions + 53 subagent sessions observed
- Sparse format: only `role` + `message.content[]` (text + tool_use blocks)

**Supplementary: `ai-code-tracking.db`** — per-code-change metadata with model info
- Path: `~/.cursor/ai-tracking/ai-code-tracking.db` (SQLite)
- `ai_code_hashes` table: 39K+ rows, `conversationId` links 100% to agent-transcript session UUIDs
- `scored_commits` table: 322 rows, commit-level AI attribution (tab/composer/human/blank line breakdown)
- Models observed: `claude-4.6-opus-high-thinking`, `composer-2-fast`, `default`, `claude-4.6-sonnet-medium-thinking`, `gpt-5.3-codex`, `grok-4-20-thinking`, `gpt-5.4-medium`

**Dead: `chats/` SQLite blob stores** — old format (Jan–Feb 2026 only, 86 sessions, no longer written to). Not targeted.

### Cursor JSONL Format vs Claude Code

Key differences that drive the sync.py rewrite:

| Aspect | Claude Code | Cursor JSONL | Cursor + tracking DB |
|--------|-------------|--------------|----------------------|
| Top-level type field | `type` ("user", "assistant") | `role` ("user", "assistant") | — |
| Message UUID | `uuid` per record | **None** — generate as `{session_id}:{line_number}` | — |
| Timestamps | `timestamp` per message | **None** — use file mtime for session timestamps | Per-code-change ms timestamps via `ai_code_hashes.timestamp` |
| Token usage | `message.usage.*` | **None** — leave as 0/NULL | **None** (confirmed absent everywhere) |
| Session ID source | `sessionId` field in records | Directory name (UUID from path) | `ai_code_hashes.conversationId` = session UUID |
| Discovery path | `~/.claude/projects/**/*.jsonl` | `~/.cursor/projects/*/agent-transcripts/**/*.jsonl` | `~/.cursor/ai-tracking/ai-code-tracking.db` |
| Content blocks | `message.content[]` | `message.content[]` (identical) | — |
| Subagent detection | `/subagents/` in path | `/subagents/` in path (identical) | — |
| Sidechain | `isSidechain` field | **None** | — |
| Model | `message.model` | **None** in JSONL | Per-request via `ai_code_hashes` join (for turns producing code changes; NULL for read-only turns) |
| AI attribution per commit | N/A | N/A | **NEW**: `scored_commits` — tab/composer/human lines + AI % |

## Component Analysis

### Affected Components

- **`scripts/schema.sql`**: DuckDB DDL → drop 5 Claude-specific tables (`deleted_sessions`, `hook_events`, `todos`, `debug_logs`, `research_history`), add `harness` column to 4 tables, add new `scored_commits` table
- **`scripts/sync.py`**: JSONL ETL pipeline → **main rewrite**: change discovery path, rewrite JSONL parser for Cursor format, remove 6 Claude-specific sync functions, add harness support. **New**: `sync_tracking_db()` reads `ai-code-tracking.db` to populate `messages.model` via `ai_code_hashes` join and import `scored_commits`
- **`scripts/query.py`**: CLI query interface → update DB_PATH, rename prog, remove `hooks` command, remove research_history from search, adapt tokens/size for no-token-data
- **`scripts/dashboard.py`**: HTTP dashboard server → update DB_PATH, remove/repurpose cost endpoints, remap tool names in trends/wrapped, update type_map for Cursor tools
- **`scripts/embed.py`**: Vector embedding generator → update DB_PATH, remove research embedding pipeline, adapt stale cleanup (no research_history)
- **`scripts/vsearch.py`**: Semantic vector search → update DB_PATH, remove research source type
- **`static/index.html`**: Dashboard frontend → rebrand to cursor-warehouse, remove/adapt cost UI sections
- **`.cursor-plugin/plugin.json`**: Plugin manifest (NEW, replaces `.claude-plugin/`)
- **`.cursor/hooks.json`**: Session hooks (NEW, Cursor uses `.cursor/hooks.json` not `hooks/hooks.json`)
- **`skills/`**: Agent skills → port 4 skills (query, recall, report, wrapped), remove costs skill, update all references
- **`README.md`**: Project documentation → rewrite for cursor-warehouse, credit upstream
- **`.gitignore`**: Copy from upstream as-is
- **`LICENSE`**: MIT license file (NEW)

### Cross-Module Dependencies

- `sync.py` → `schema.sql`: executes DDL to init DB
- `sync.py` → `ai-code-tracking.db`: reads model info + scored commits (SQLite, read-only)
- `query.py`, `dashboard.py`, `embed.py`, `vsearch.py` → DB tables defined in `schema.sql`
- `embed.py` → `schema.sql`: also inits DB + manages HNSW index
- `dashboard.py` → `static/index.html`: serves static files
- `query.py` → `vsearch.py`: delegates `vsearch` subcommand
- Skills → scripts: skills reference script paths via `${CURSOR_PLUGIN_ROOT}/scripts/`

### Boundary Changes

- **Schema**: 5 tables dropped, `harness` column added to 4 tables, `scored_commits` table added
- **Sessions table**: drops `git_branch`, `version`, `cwd`, token columns (set to 0/NULL since Cursor doesn't provide them)
- **Messages table**: drops `parent_uuid`, `is_sidechain` (set to NULL/FALSE), token columns. `model` column now populated via `ai-code-tracking.db` join (for code-producing turns)
- **New `scored_commits` table**: Cursor-specific — commit-level AI attribution (tab/composer/human line counts + AI percentages)
- **Sync data sources**: Two inputs — JSONL transcripts (primary) + `ai-code-tracking.db` (supplementary, model + commits)
- **CLI**: `hooks` command removed, prog renamed from `cw` to `cursor-warehouse`
- **Dashboard API**: `/api/costs` repurposed as session-count-by-project (no token cost data)

### Invariants & Constraints

1. MIT license preserved on all ported files
2. PEP 723 script pattern preserved (`uv run --script`)
3. DuckDB as warehouse engine (unchanged)
4. sentence-transformers + torch for embeddings (unchanged)
5. Chart.js dashboard (unchanged)
6. HTTP server on port 3141 (unchanged)
7. HNSW index for vector similarity (unchanged)
8. `harness` column defaults to `'cursor'` on all provenance tables
9. No pyproject.toml / uv.lock (VISION2 scope)

## Open Questions

None — implementation approach is clear. VISIONA provides detailed specifications for every component. Data format analysis complete:
- Cursor JSONL format verified against 218 files / 7,620 records
- `ai-code-tracking.db` schema verified against live database (39K+ rows)
- `chats/` format investigated and confirmed dead (Jan–Feb 2026 only, not targeted)
- Model availability confirmed via `ai_code_hashes` join (7 distinct models observed)

## Test Plan (TDD)

### Behaviors to Verify

**Schema (`test_schema.py`):**
- Schema DDL executes without error on fresh DuckDB
- Tables `_sync_state`, `sessions`, `messages`, `tool_calls`, `embeddings`, `scored_commits` exist
- Tables `deleted_sessions`, `hook_events`, `todos`, `debug_logs`, `research_history` do NOT exist
- `harness` column exists with default `'cursor'` on `sessions`, `messages`, `tool_calls`, `embeddings`, `scored_commits`
- `scored_commits` has correct columns: `commit_hash`, `branch_name`, `harness`, `scored_at`, line counts (tab/composer/human/blank added/deleted), `commit_message`, `commit_date`, `v1_ai_percentage`, `v2_ai_percentage`

**Sync — JSONL Parser (`test_sync.py`):**
- Cursor JSONL with `role` field (not `type`) is parsed correctly
- Message UUIDs generated as `{session_id}:{line_number}` are stable and deterministic
- Session ID derived from directory name (UUID from path)
- `message.content[]` text blocks extracted correctly
- `message.content[]` tool_use blocks extracted into tool_calls table
- Token counts default to 0 (not available in Cursor)
- `harness` column set to `'cursor'` on all inserted rows
- Subagent detection via `/subagents/` path works
- Parent session ID derived from subagent path structure
- Empty JSONL files handled gracefully (no crash, no rows)
- Malformed JSON lines skipped without crash
- First user prompt extracted correctly for session summary

**Sync — Discovery (`test_sync.py`):**
- `_scan_jsonl_files()` finds files under `agent-transcripts/` structure
- Watermark system filters out already-processed files
- Multiple workspace slugs are discovered

**Sync — Tracking DB Integration (`test_sync.py`):**
- `sync_tracking_db()` reads `ai-code-tracking.db` and populates `messages.model` via `ai_code_hashes.conversationId` + `requestId` join
- Model populated on messages whose request produced code changes; NULL for read-only turns
- Multi-model conversations: different models on different turns within same session
- `scored_commits` rows imported with correct column mapping (camelCase → snake_case)
- Dedup: re-syncing tracking DB doesn't create duplicate `scored_commits` rows
- Missing tracking DB: sync completes gracefully (logs warning, model stays NULL, no scored_commits)
- Tracking DB path discovery: finds `~/.cursor/ai-tracking/ai-code-tracking.db`

**Sync — Removed Functions:**
- No `sync_deleted_sessions`, `sync_hook_events`, `sync_todos`, `sync_debug`, `sync_history`, `purge_synced_files` functions exist

**Edge cases:**
- JSONL file with only assistant messages (no user turn) → still creates session
- JSONL with 0 bytes → skip gracefully
- Session with content blocks that are plain strings (not dicts)
- Very long text_content → truncated to 2000 chars
- Tracking DB locked by active Cursor process → graceful skip with warning

### Test Infrastructure

- Framework: pytest
- Run command: `uv run --with pytest --with duckdb pytest tests/ -v`
- Test location: `tests/`
- Fixtures: `tests/fixtures/` with sample Cursor JSONL files and a sample `ai-code-tracking.db`
- New test files:
  - `tests/conftest.py` — adds `scripts/` to `sys.path`, provides DuckDB fixtures, provides sample tracking DB fixture
  - `tests/test_schema.py` — schema validation (includes `scored_commits` table)
  - `tests/test_sync.py` — JSONL parser, tracking DB sync, and full sync flow

### Integration Tests

- **Full sync flow**: Create temp directory mimicking `~/.cursor/projects/` structure with sample JSONL files + sample `ai-code-tracking.db`, run sync, verify all tables populated correctly including `messages.model` and `scored_commits`
- **Dedup**: Sync same file twice, verify no duplicate rows (JSONL and scored_commits)
- **Tracking DB missing**: Full sync completes successfully when `ai-code-tracking.db` does not exist (model stays NULL, scored_commits empty)

## Implementation Plan

### Phase 1: Foundation (schema + test infra)

1. **Copy `.gitignore` from upstream**
    - Files: `.gitignore`
    - Changes: copy as-is from upstream

2. **Create `scripts/schema.sql`**
    - Files: `scripts/schema.sql`
    - Changes: Copy upstream, drop 5 Claude-specific table definitions (`deleted_sessions`, `hook_events`, `todos`, `debug_logs`, `research_history`), add `harness TEXT NOT NULL DEFAULT 'cursor'` to `sessions`, `messages`, `tool_calls`, `embeddings`. Add new `scored_commits` table with `commit_hash`/`branch_name` PK, `harness`, line counts (tab/composer/human/blank), `commit_message`, `commit_date`, AI percentages.

3. **Create test infrastructure + `tests/test_schema.py`**
    - Files: `tests/conftest.py`, `tests/test_schema.py`, `tests/fixtures/` (empty for now)
    - Changes: conftest.py sets up sys.path and DuckDB fixtures; test_schema.py validates table existence (including `scored_commits`), harness column defaults, absence of dropped tables, `scored_commits` column structure

4. **Run schema tests** — should pass immediately since schema.sql is just DDL

### Phase 2: Sync Engine (main rewrite — TDD)

5. **Create test fixtures**
    - Files: `tests/fixtures/cursor_session.jsonl`, `tests/fixtures/cursor_subagent.jsonl`, `tests/fixtures/empty.jsonl`, `tests/fixtures/malformed.jsonl`
    - Changes: Hand-craft sample Cursor-format JSONL files based on verified format

6. **Write + stub `tests/test_sync.py`**
    - Files: `tests/test_sync.py`
    - Changes: Full test implementations covering all behaviors listed in test plan

7. **Copy and stub `scripts/sync.py`**
    - Files: `scripts/sync.py`
    - Changes: Copy upstream, change paths/constants, stub out Cursor-specific parser (empty body), remove Claude-specific functions

8. **Run tests** — all sync tests should fail (TDD red phase)

9. **Implement `scripts/sync.py` Cursor JSONL parser**
    - Files: `scripts/sync.py`
    - Changes:
      - `CURSOR_DIR = Path.home() / ".cursor"`, `DB_PATH = CURSOR_DIR / "cursor-warehouse.duckdb"`
      - Discovery: `~/.cursor/projects/*/agent-transcripts/**/*.jsonl`
      - `_ingest_jsonl()`: parse `role` (not `type`), generate UUID as `{session_id}:{line_idx}`, skip `uuid`/`timestamp`/`sessionId` record fields, default tokens to 0, set `harness='cursor'`
      - `_scan_jsonl_files()`: scan `agent-transcripts/` directories
      - `sync_sessions()` / `sync_subagents()`: adapt for Cursor path structure
      - `main()`: remove calls to deleted sync functions, remove `--purge` flag
      - Session metadata: `project_name` derived from workspace slug, `project_path` from transcript directory

10. **Run tests iteratively** until all pass (TDD green phase)

### Phase 2b: Tracking DB Integration (TDD)

11. **Create tracking DB test fixture**
    - Files: `tests/fixtures/sample_tracking.db`
    - Changes: Create a minimal SQLite database matching `ai-code-tracking.db` schema with `ai_code_hashes` rows (multiple models, multiple requestIds per conversationId) and `scored_commits` rows

12. **Write tracking DB tests in `tests/test_sync.py`**
    - Files: `tests/test_sync.py`
    - Changes: Add test cases for:
      - `sync_tracking_db()` populates `messages.model` via join
      - Multi-model conversations get correct per-request model
      - `scored_commits` imported with snake_case column mapping
      - Dedup on re-sync
      - Missing tracking DB → graceful skip
      - Locked tracking DB → graceful skip with warning

13. **Run tests** — tracking DB tests should fail (TDD red phase)

14. **Implement `sync_tracking_db()` in `scripts/sync.py`**
    - Files: `scripts/sync.py`
    - Changes:
      - `_find_tracking_db()`: discover `ai-code-tracking.db` path (check `~/.cursor/ai-tracking/` on both WSL and Windows-mapped paths)
      - `_sync_model_from_tracking()`: open tracking DB read-only, join `ai_code_hashes` on `conversationId = session_id`, group by `(conversationId, requestId)` to get model per request, UPDATE `messages.model` for matching messages (correlate by timestamp range within the session)
      - `_sync_scored_commits()`: read `scored_commits` table, INSERT OR REPLACE into DuckDB `scored_commits` with snake_case column names and `harness='cursor'`
      - `main()`: call `sync_tracking_db()` after JSONL sync, wrapped in try/except for graceful failure

15. **Run tests iteratively** until all pass (TDD green phase)

### Phase 3: Query Layer

16. **Copy and modify `scripts/query.py`**
    - Files: `scripts/query.py`
    - Changes:
      - `DB_PATH = Path.home() / ".cursor" / "cursor-warehouse.duckdb"`
      - `prog="cursor-warehouse"` in argparse
      - Remove `cmd_hooks` function and `hooks` subcommand
      - `cmd_tokens`: gracefully handle 0 token counts, show "N/A" or 0
      - `cmd_search`: remove `research_history` query
      - `cmd_size`: remove `hook_events`, `todos`, `debug_logs`, `research_history` from table list; add `scored_commits`
      - `cmd_vsearch`: update plugin path discovery for cursor-warehouse
      - Docstring: `cursor-warehouse` references

17. **Copy and modify `scripts/dashboard.py`**
    - Files: `scripts/dashboard.py`
    - Changes:
      - `DB_PATH = Path.home() / ".cursor" / "cursor-warehouse.duckdb"`
      - `api_overview()`: remove cost calculation, show session/message counts; add model distribution summary
      - `api_costs()`: repurpose as session-count-by-project (no token cost data)
      - `api_trends()`: remap tool names — writes: `Write`, `StrReplace`, `EditNotebook`; reads: `Read`, `Glob`, `Grep`, `SemanticSearch`
      - `api_efficiency()`: remove `avg_cost_usd` from prompt quality
      - `api_wrapped()`: update `type_map` for Cursor tools (`Write`→"The Architect", `Read`→"The Scholar", `Shell`→"The Hacker", `StrReplace`→"The Surgeon", `Grep`→"The Detective", `Glob`→"The Explorer", `Task`→"The Orchestrator", `SemanticSearch`→"The Researcher")
      - New: `api_ai_attribution()`: serve `scored_commits` data for AI % visualization
      - Docstring: `cursor-warehouse` references

18. **Copy and modify `static/index.html`**
    - Files: `static/index.html`
    - Changes:
      - Title: `cursor-warehouse`
      - Header: `cursor-warehouse` branding
      - Remove "Est. Cost" overview card
      - Cost chart section → "Sessions by Project" (bar chart of session counts)
      - Remove `fmtUsd` references where token cost data is unavailable
      - Prompt quality chart: remove cost axis, show avg messages instead
      - New: AI Attribution section — chart showing `scored_commits` AI % over time

### Phase 4: Embeddings

19. **Copy and modify `scripts/embed.py`**
    - Files: `scripts/embed.py`
    - Changes:
      - `DB_PATH = Path.home() / ".cursor" / "cursor-warehouse.duckdb"`
      - Remove `embed_research()` function and `res_count` from `count_unembedded()`
      - Remove stale research cleanup from `clean_stale_embeddings()`
      - `main()`: remove research embedding call
      - Docstring: `cursor-warehouse` references

20. **Copy and modify `scripts/vsearch.py`**
    - Files: `scripts/vsearch.py`
    - Changes:
      - `DB_PATH = Path.home() / ".cursor" / "cursor-warehouse.duckdb"`
      - Remove `"research"` from `--type` choices
      - Remove research case from `enrich()` function
      - Docstring: `cursor-warehouse` references

### Phase 5: Packaging, Skills, Documentation

21. **Create `.cursor-plugin/plugin.json`**
    - Files: `.cursor-plugin/plugin.json`
    - Changes: New file with cursor-warehouse metadata, version 0.1.0, MIT license

22. **Create `hooks/hooks.json`**
    - Files: `hooks/hooks.json`
    - Changes: Cursor-format hooks file (version 1, `sessionStart` event — camelCase per Cursor convention) with sync + dashboard commands using `${CURSOR_PLUGIN_ROOT}` paths. README will document manual `.cursor/hooks.json` setup for non-plugin installs.

23. **Port skills**
    - Files:
      - `skills/query/SKILL.md` — update schema refs (remove dropped tables, add scored_commits), update paths, rename references
      - `skills/recall/SKILL.md` — update paths, rename references
      - `skills/report/SKILL.md` — remove cost queries, adapt for no token data, update tool names, add model distribution + AI attribution queries
      - `skills/wrapped/SKILL.md` — remap tool-to-personality for Cursor tools, remove cost queries
    - Removed: `skills/costs/SKILL.md` — not ported (no token data from Cursor)

24. **Create `LICENSE`**
    - Files: `LICENSE`
    - Changes: MIT license text

25. **Write `README.md`**
    - Files: `README.md`
    - Changes: cursor-warehouse overview, installation, usage, data sources (JSONL + tracking DB), credits to upstream claude-warehouse

### Phase 6: Global Verification

26. **Global search-and-replace verification**
    - Verify no remaining references to: `claude-warehouse`, `claude_warehouse`, `~/.claude` (as data path), `CLAUDE_DIR`, `CLAUDE_PLUGIN_ROOT`, `claude.duckdb`, `cw` (as CLI prog name)

27. **Full test suite run**
    - `uv run --with pytest --with duckdb pytest tests/ -v`

28. **Manual smoke test**
    - Run `uv run --script scripts/sync.py -v` against local Cursor transcripts
    - Run `uv run --script scripts/query.py sessions` — verify `model` column populated
    - Run `uv run --script scripts/query.py size` — verify `scored_commits` table has rows
    - Verify dashboard serves at localhost:3141

## Technology Validation

No new technology — all dependencies are inherited from upstream:
- `duckdb>=1.2` — DuckDB for warehousing
- `sentence-transformers>=3.0` + `torch` — for embed.py and vsearch.py
- `pytest` — for test runner (run via `uv run --with`)

Validation: The upstream already validates these dependencies work with PEP 723. We inherit that validation.

## Challenges & Mitigations

- **JSONL parser correctness**: The main risk. Cursor's format lacks UUIDs and timestamps, requiring synthetic generation. Mitigation: comprehensive TDD tests with real fixture data based on verified Cursor transcript format.
- **No per-message timestamps in JSONL**: Cursor JSONL has no timestamps per message, only file mtime. However, `ai-code-tracking.db` provides ms-precision timestamps for code-producing turns. Mitigation: use file mtime for session-level timestamps, enrich with tracking DB timestamps where available, document limitation for non-code turns.
- **Model info is partial**: Only available for turns that produce code changes (via `ai-code-tracking.db`). Read-only turns (search, discussion) will have NULL model. Mitigation: populate where available, leave NULL otherwise; still far better than all-NULL. Multi-model conversations are tracked per-request.
- **No token data**: Cost calculations, token usage charts, and prompt-quality cost correlations are meaningless. Mitigation: remove/repurpose cost UI, show "N/A" where appropriate, repurpose cost-by-project as sessions-by-project.
- **Tracking DB cross-platform path**: `ai-code-tracking.db` lives at `~/.cursor/ai-tracking/` which on WSL maps to the Windows user profile. Path discovery must handle both native Linux and WSL-to-Windows paths. Mitigation: check multiple candidate paths; log which one was found.
- **Tracking DB locking**: Active Cursor process may lock the SQLite database. Mitigation: open with `?immutable=1` URI flag or copy to temp location; gracefully skip if inaccessible.
- **Cursor plugin/hooks format uncertainty**: Cursor's plugin manifest format (`.cursor-plugin/plugin.json`) may differ from Claude's. Mitigation: follow VISIONA's spec; Cursor's hooks system is confirmed to exist (`.cursor/hooks.json` with `sessionStart` event).
- **Workspace slug decoding**: Deriving human-readable project names from workspace slugs (e.g., `home-mobaxterm-Documents-git-myproject` → `myproject`) requires heuristic parsing. Mitigation: take last segment after splitting on hyphens that correspond to path separators; good enough for display, not critical.

## Status

- [x] Component analysis complete
- [x] Open questions resolved
- [x] Test planning complete (TDD)
- [x] Implementation plan complete (revised: +5 steps for tracking DB integration, 28 total)
- [x] Technology validation complete
- [x] Preflight (PASS — hooks location corrected)
- [x] Plan revision: `ai-code-tracking.db` integration (model, scored_commits)
- [x] Build (28/28 steps complete, 43 tests passing, smoke test verified)
- [x] QA (PASS — 5 trivial fixes applied, 0 blocking issues)
