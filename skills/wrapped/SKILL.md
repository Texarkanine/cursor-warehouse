---
name: wrapped
description: Generate a fun, shareable summary of your Cursor agent usage stats — like Spotify Wrapped but for AI-assisted development. Use when the user asks for their stats, summary, wrapped, or wants a fun overview of their Cursor activity.
---

# Wrapped — Your Cursor Year in Review

Generate a fun, visual summary of the user's Cursor agent activity. Think Spotify Wrapped energy.

Run ALL queries below, then present as an engaging, shareable summary.

## Schema reference (for adapting queries)

When the user specifies a date range, add WHERE clauses. Use these join paths:

```
sessions.session_id  ←→  messages.session_id
sessions.session_id  ←→  tool_calls.session_id
messages.uuid        ←→  tool_calls.message_uuid   (NOT message_id)
```

To filter `tool_calls` or `messages` by date, JOIN through `sessions`:
```sql
SELECT tc.tool_name, COUNT(*) uses
FROM tool_calls tc
JOIN sessions s ON tc.session_id = s.session_id
WHERE s.created_at >= '...' AND s.created_at < '...'
GROUP BY 1 ORDER BY 2 DESC

SELECT model, COUNT(*) messages
FROM messages m
JOIN sessions s ON m.session_id = s.session_id
WHERE model IS NOT NULL AND s.created_at >= '...' AND s.created_at < '...'
GROUP BY 1 ORDER BY 2 DESC
```

## Data collection

### All-time stats
```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT COUNT(*) total_sessions, SUM(message_count) total_messages, COUNT(DISTINCT project_name) total_projects, MIN(created_at)::DATE first_session, MAX(created_at)::DATE latest_session FROM sessions"
```

### Top projects by session count
```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT project_name, COUNT(*) sessions, SUM(message_count) messages FROM sessions GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
```

### Favorite tools (top 10)
```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT tc.tool_name, COUNT(*) uses FROM tool_calls tc GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
```

### Longest session ever
```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT project_name, created_at::DATE, message_count, LEFT(first_prompt, 100) prompt FROM sessions ORDER BY message_count DESC LIMIT 1"
```

### Busiest day
```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT created_at::DATE as day, COUNT(*) sessions, SUM(message_count) messages FROM sessions GROUP BY 1 ORDER BY 2 DESC LIMIT 1"
```

### Models used
```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT model, COUNT(*) messages FROM messages WHERE model IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
```

### Streak (consecutive days)
```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "WITH days AS (SELECT DISTINCT created_at::DATE as d FROM sessions), streaks AS (SELECT d, d - ROW_NUMBER() OVER (ORDER BY d) * INTERVAL '1 day' as grp FROM days) SELECT COUNT(*) as streak_days, MIN(d)::DATE as from_date, MAX(d)::DATE as to_date FROM streaks GROUP BY grp ORDER BY streak_days DESC LIMIT 1"
```

### Session distribution by hour of day
```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT EXTRACT(HOUR FROM created_at) as hour, COUNT(*) sessions FROM sessions GROUP BY 1 ORDER BY 2 DESC LIMIT 3"
```

### AI attribution (if available)
```bash
${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "SELECT COUNT(*) total_commits, SUM(lines_added) total_lines, AVG(CAST(REPLACE(COALESCE(v2_ai_percentage, '0'), '%', '') AS FLOAT)) avg_ai_pct FROM scored_commits"
```

## Presentation

Present as a **fun, engaging summary** with personality. Use section headers like:

- **Your Numbers** — total sessions, messages, projects
- **#1 Project** — your most-visited project and what it says about you
- **Power Tools** — your top 5 tools and what that means
- **Marathon Session** — your longest session: what happened?
- **Peak Hours** — when you do your best AI-assisted work
- **Your Streak** — longest consecutive days using Cursor
- **AI Contribution** — how much of your code is AI-generated
- **Your Type** — categorize them based on patterns:
  - "The Architect" — Write-heavy, designs from scratch
  - "The Scholar" — Read-heavy, studies code deeply
  - "The Hacker" — Shell-heavy, command-line warrior
  - "The Surgeon" — StrReplace-heavy, precise edits
  - "The Detective" — Grep-heavy, finds the clues
  - "The Explorer" — Glob-heavy, navigates the codebase
  - "The Orchestrator" — Task-heavy, delegates to subagents
  - "The Researcher" — SemanticSearch-heavy, finds by meaning

Keep it concise, punchy, fun. Something they'd screenshot and share.
