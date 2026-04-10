---
description: Generate an analytics report on your AI-assisted development habits. Use when the user asks about their productivity, workflow patterns, session efficiency, or wants to understand how they use Cursor. Covers model usage, tool patterns, session shapes, project maturity, AI attribution, and actionable improvement suggestions.
---

# Report — AI Development Analytics

Generate a comprehensive analytics report on the user's Cursor agent usage patterns.

Run ALL of the following queries and synthesize the results into a clear, actionable report.

## Schema reference (for adapting queries)

When the user specifies a date range, add WHERE clauses. Use these join paths:

```
sessions.session_id  ←→  messages.session_id
sessions.session_id  ←→  tool_calls.session_id
messages.uuid        ←→  tool_calls.message_uuid   (NOT message_id)
```

Key column differences between tables:
- **sessions**: `created_at` (TIMESTAMP), `modified_at`
- **messages**: `timestamp` (TIMESTAMP) — NO created_at column
- **tool_calls**: `timestamp` (TIMESTAMP) — NO created_at column
- **Ambiguous columns**: `tool_name` exists in BOTH messages and tool_calls — always qualify with table alias

To filter `tool_calls` or `messages` by date, JOIN through `sessions`:
```sql
SELECT tc.tool_name, COUNT(*) FROM tool_calls tc JOIN sessions s ON tc.session_id = s.session_id WHERE s.created_at >= '...' GROUP BY 1
SELECT model, COUNT(*) FROM messages m JOIN sessions s ON m.session_id = s.session_id WHERE s.created_at >= '...' GROUP BY 1
```

## 1. Overview (last 30 days vs previous 30 days)

```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT 'last_30d' as period, COUNT(*) sessions, SUM(message_count) messages, ROUND(AVG(message_count), 1) avg_msgs, COUNT(DISTINCT project_name) projects FROM sessions WHERE created_at >= current_date - INTERVAL '30 days' UNION ALL SELECT 'prev_30d', COUNT(*), SUM(message_count), ROUND(AVG(message_count), 1), COUNT(DISTINCT project_name) FROM sessions WHERE created_at >= current_date - INTERVAL '60 days' AND created_at < current_date - INTERVAL '30 days'"
```

## 2. Model distribution (last 30 days)

```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT COALESCE(m.model, 'unknown') model, COUNT(*) messages, COUNT(DISTINCT m.session_id) sessions FROM messages m JOIN sessions s ON m.session_id = s.session_id WHERE s.created_at >= current_date - INTERVAL '30 days' GROUP BY 1 ORDER BY 2 DESC"
```

## 3. Session efficiency (short vs long, abandoned sessions)

```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT CASE WHEN message_count <= 3 THEN 'abandoned (1-3 msgs)' WHEN message_count <= 10 THEN 'short (4-10 msgs)' WHEN message_count <= 30 THEN 'medium (11-30 msgs)' ELSE 'long (30+ msgs)' END as bucket, COUNT(*) sessions, ROUND(AVG(message_count), 1) avg_msgs FROM sessions WHERE created_at >= current_date - INTERVAL '30 days' GROUP BY 1 ORDER BY MIN(message_count)"
```

## 4. Tool usage distribution

```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT tc.tool_name, COUNT(*) calls, ROUND(COUNT(*)::FLOAT / SUM(COUNT(*)) OVER () * 100, 1) as pct FROM tool_calls tc JOIN sessions s ON tc.session_id = s.session_id WHERE s.created_at >= current_date - INTERVAL '30 days' GROUP BY 1 ORDER BY 2 DESC LIMIT 15"
```

## 5. Write/Read ratio trend (AI collaboration maturity)

```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT DATE_TRUNC('week', s.created_at)::DATE as week, COUNT(*) FILTER (WHERE tc.tool_name IN ('Write', 'StrReplace', 'EditNotebook')) as writes, COUNT(*) FILTER (WHERE tc.tool_name IN ('Read', 'Glob', 'Grep', 'SemanticSearch')) as reads, ROUND(COUNT(*) FILTER (WHERE tc.tool_name IN ('Write', 'StrReplace', 'EditNotebook'))::FLOAT / NULLIF(COUNT(*) FILTER (WHERE tc.tool_name IN ('Read', 'Glob', 'Grep', 'SemanticSearch')), 0), 2) as write_read_ratio FROM tool_calls tc JOIN sessions s ON tc.session_id = s.session_id GROUP BY 1 ORDER BY 1 DESC LIMIT 8"
```

## 6. First prompt quality signal

```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT CASE WHEN LENGTH(first_prompt) < 50 THEN 'short (<50 chars)' WHEN LENGTH(first_prompt) < 200 THEN 'medium (50-200)' ELSE 'detailed (200+)' END as prompt_length, COUNT(*) sessions, ROUND(AVG(message_count), 1) avg_msgs FROM sessions WHERE created_at >= current_date - INTERVAL '30 days' AND first_prompt IS NOT NULL GROUP BY 1 ORDER BY MIN(LENGTH(first_prompt))"
```

## 7. AI attribution summary (from scored_commits)

```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT branch_name, COUNT(*) commits, SUM(lines_added) total_added, SUM(composer_lines_added + tab_lines_added) ai_lines, SUM(human_lines_added) human_lines, AVG(CAST(REPLACE(COALESCE(v2_ai_percentage, '0'), '%', '') AS FLOAT)) avg_ai_pct FROM scored_commits GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
```

## 8. Daily activity heatmap

```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT created_at::DATE as day, COUNT(*) sessions, SUM(message_count) msgs FROM sessions WHERE created_at >= current_date - INTERVAL '14 days' GROUP BY 1 ORDER BY 1 DESC"
```

## Report format

Present the results as a structured report with:

1. **Summary**: Key numbers (sessions, messages, active projects, models used)
2. **Trends**: Compare to previous period — what's changing?
3. **Efficiency**: Session shape distribution, abandoned session rate, first-prompt quality correlation
4. **Tool patterns**: What the agent spends time doing, write/read ratio trend
5. **Model usage**: Which models are used most, distribution across sessions
6. **AI Attribution**: Per-branch AI percentages, human vs AI code contribution
7. **Actionable suggestions**: 3-5 specific things to improve based on the data:
   - High abandoned rate → prompts need more specificity
   - Long sessions dominating → break tasks into smaller units
   - Read-heavy tool usage → improve project discoverability
   - Low AI attribution scores → consider using AI more for boilerplate
   - Single model dominance → consider trying other models for different tasks
