---
name: "cw:query"
description: Run raw SQL queries against the Cursor agent session DuckDB warehouse. Use when you need precise, structured lookups across sessions, messages, tool calls, scored commits, or embeddings that go beyond simple text search.
---

# Query — Raw SQL on the Warehouse

Run arbitrary SQL against the local DuckDB warehouse containing all Cursor agent session data.

## Finding the script

`CURSOR_PLUGIN_ROOT` should be set when invoked through the plugin system, but may be unset during development. Resolve once per session:

```bash
QUERY_SCRIPT="${CURSOR_PLUGIN_ROOT:+$CURSOR_PLUGIN_ROOT/scripts/query.py}"
if [ -z "$QUERY_SCRIPT" ] || [ ! -f "$QUERY_SCRIPT" ]; then
  QUERY_SCRIPT="$(find ~/.cursor/plugins -name query.py -path '*/cursor-warehouse/*/query.py' 2>/dev/null | head -1)"
fi
```

Once resolved, run queries with:
```bash
uv run --script "$QUERY_SCRIPT" sql "$SQL"
```

The script also has built-in subcommands: `sessions`, `tools`, `search`, `projects`, `size`, `tokens`, `sql`.

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

## Critical: text_content and first_prompt contain system context

User messages in Cursor are wrapped in XML system context: `<user_query>`, `<manually_attached_skills>`, `<rules>`, `<open_and_recently_viewed_files>`, etc. The `text_content` and `first_prompt` fields store the **full raw message** including all this framing. A naive `substr(text_content, 1, 200)` will almost always return system XML, not the user's actual words.

### Extracting actual user intent

Use `regexp_extract` to pull the `<user_query>` tag content:
```sql
regexp_extract(m.text_content, '<user_query>\s*([\s\S]*?)\s*</user_query>', 1)
```

**Skill-invocation sessions** (e.g. `/cw-wrapped`, `/cw-report`) may not have `<user_query>` tags at all — the user message is just the skill's `<manually_attached_skills>` block plus the invocation command. For these, search for the `/cw-` or `/command` pattern:
```sql
regexp_extract(m.text_content, '(/\S+[^\n<]*)', 1)
```

### Combining both patterns

To get a usable "what was this about?" summary for any session:
```sql
SELECT
  s.session_id,
  COALESCE(
    NULLIF(regexp_extract(m.text_content, '<user_query>\s*([\s\S]*?)\s*</user_query>', 1), ''),
    NULLIF(regexp_extract(m.text_content, '(/\S+[^\n<]*)', 1), ''),
    LEFT(m.text_content, 200)
  ) AS user_intent
FROM sessions s
JOIN messages m ON s.session_id = m.session_id
  AND m.role = 'user'
  AND m.timestamp = (SELECT MIN(m2.timestamp) FROM messages m2 WHERE m2.session_id = s.session_id AND m2.role = 'user')
ORDER BY s.created_at DESC
LIMIT 10
```

### Output truncation

The `query.py sql` subcommand truncates columns to **60 characters** by default. When you need longer content, widen the output by truncating within the SQL itself at a length you control:
```sql
LEFT(regexp_extract(m.text_content, '<user_query>\s*([\s\S]*?)\s*</user_query>', 1), 500) AS user_query
```

This way the 60-char display truncation still applies, but you can SELECT multiple rows and still get meaningful previews. For truly long content, select a single row with no artificial truncation and the display will wrap.

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

Recent sessions with actual user intent:
```sql
SELECT
  s.session_id, s.created_at, s.message_count, s.project_name,
  COALESCE(
    NULLIF(regexp_extract(m.text_content, '<user_query>\s*([\s\S]*?)\s*</user_query>', 1), ''),
    NULLIF(regexp_extract(m.text_content, '(/\S+[^\n<]*)', 1), ''),
    LEFT(m.text_content, 200)
  ) AS user_intent
FROM sessions s
LEFT JOIN (
  SELECT session_id, text_content,
    ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY timestamp) AS rn
  FROM messages WHERE role = 'user'
) m ON s.session_id = m.session_id AND m.rn = 1
WHERE s.is_subagent = false
ORDER BY s.created_at DESC LIMIT 10
```

Frustration signals (user corrections/pushback):
```sql
SELECT s.session_id, s.project_name, COUNT(*) frustration_msgs
FROM messages m
JOIN sessions s ON m.session_id = s.session_id
WHERE m.role = 'user'
  AND (m.text_content ILIKE '%no dice%' OR m.text_content ILIKE '%not even close%'
       OR m.text_content ILIKE '%did NOT fix%' OR m.text_content ILIKE '%ARGH%'
       OR m.text_content ILIKE '%that is wrong%' OR m.text_content ILIKE '%still broken%')
GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10
```
