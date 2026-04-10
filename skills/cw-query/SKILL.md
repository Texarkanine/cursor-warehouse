---
name: "cw:query"
description: Run raw SQL queries against the Cursor agent session DuckDB warehouse. Use when you need precise, structured lookups across sessions, messages, tool calls, scored commits, or embeddings that go beyond simple text search.
---

# Query — Raw SQL on the Warehouse

Run arbitrary SQL against the local DuckDB warehouse containing all Cursor agent session data.

## Usage

```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "$ARGUMENTS"
```

## Schema

**sessions** — One row per session
- `session_id` (PK), `harness`, `project_path`, `project_name`
- `created_at` (TIMESTAMP), `modified_at` (TIMESTAMP), `message_count`
- `tools_used` (JSON array), `models_used` (JSON array)
- `first_prompt`, `file_path`
- `is_subagent`, `parent_session_id`

**messages** — Individual turns from session JSONL
- `session_id`, `uuid` (PK with session_id), `harness`, `type`, `timestamp` (TIMESTAMP)
- `role`, `model`
- `content_types` (JSON), `tool_name`, `text_content`
- NOTE: `model` is populated via `ai-code-tracking.db` join (code-producing turns only; NULL for read-only turns)
- NOTE: No `created_at` column — use `timestamp` or JOIN to sessions

**tool_calls** — Extracted tool invocations
- `session_id`, `message_uuid` (FK → messages.uuid), `idx`, `harness`, `tool_name`, `tool_input`, `timestamp` (TIMESTAMP)
- NOTE: No `created_at` column — use `timestamp` or JOIN to sessions

**scored_commits** — Commit-level AI attribution from ai-code-tracking.db
- `commit_hash`, `branch_name` (PK), `harness`, `scored_at`
- `lines_added`, `lines_deleted`
- `tab_lines_added`, `tab_lines_deleted`, `composer_lines_added`, `composer_lines_deleted`
- `human_lines_added`, `human_lines_deleted`, `blank_lines_added`, `blank_lines_deleted`
- `commit_message`, `commit_date`, `v1_ai_percentage`, `v2_ai_percentage`

**embeddings** — Vector embeddings for semantic search
- `source_type` ('message'|'session'), `source_id`, `chunk_idx`, `harness`, `text_preview`, `embedding` (FLOAT[384])
- source_id encoding: `uuid` (format `{session_id}:{line_idx}`) for messages, `session_id` for sessions

## Join paths

```text
sessions.session_id  ←→  messages.session_id
sessions.session_id  ←→  tool_calls.session_id
messages.uuid        ←→  tool_calls.message_uuid   (NOT message_id)
```

**Ambiguous columns**: `tool_name` exists in BOTH messages and tool_calls — always qualify with table alias (e.g., `tc.tool_name`).

## Example queries

Model usage last 7 days:
```sql
SELECT COALESCE(m.model, 'unknown') model, COUNT(*) messages FROM messages m JOIN sessions s ON m.session_id = s.session_id WHERE s.created_at >= current_date - INTERVAL '7 days' GROUP BY 1 ORDER BY 2 DESC
```

Most used tools:
```sql
SELECT tc.tool_name, COUNT(*) calls FROM tool_calls tc JOIN sessions s ON tc.session_id = s.session_id WHERE s.created_at >= current_date - INTERVAL '7 days' GROUP BY 1 ORDER BY 2 DESC LIMIT 20
```

AI attribution summary:
```sql
SELECT branch_name, COUNT(*) commits, AVG(CAST(REPLACE(v2_ai_percentage, '%', '') AS FLOAT)) avg_ai_pct FROM scored_commits GROUP BY 1 ORDER BY 2 DESC
```

Sessions for a project:
```sql
SELECT session_id, created_at, message_count, first_prompt FROM sessions WHERE project_name ILIKE '%myproject%' ORDER BY created_at DESC LIMIT 10
```
