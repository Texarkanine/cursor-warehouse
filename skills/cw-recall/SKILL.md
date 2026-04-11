---
name: "cw:recall"
description: Search across all past Cursor agent sessions. Use when you need to recall previous work, find solutions to problems you've solved before, or retrieve context from past conversations. Powered by DuckDB keyword search and semantic vector search over session history.
---

# Recall — Cross-Session Memory

Search across all past Cursor agent sessions stored in the local DuckDB warehouse.

## When to use

- "Have I worked on this before?"
- "How did I solve X last time?"
- "What did I do in project Y?"
- "Find all sessions where we discussed Z"

## Finding the scripts

`CURSOR_PLUGIN_ROOT` should be set when invoked through the plugin system, but may be unset during development. Resolve once per session:

Use `find -L` so symlinked dev installs (e.g. `local/cursor-warehouse` → your clone) are traversed.

```bash
PLUGIN_SCRIPTS="${CURSOR_PLUGIN_ROOT:+$CURSOR_PLUGIN_ROOT/scripts}"
if [ -z "$PLUGIN_SCRIPTS" ] || [ ! -d "$PLUGIN_SCRIPTS" ]; then
  PLUGIN_SCRIPTS="$(dirname "$(find -L ~/.cursor/plugins -name query.py -path '*/cursor-warehouse/*/query.py' 2>/dev/null | head -1)")"
fi
```

## How to search

Always run BOTH searches — they complement each other.

### 1. Keyword search (exact substring match)
```bash
uv run --script "$PLUGIN_SCRIPTS/query.py" search "$ARGUMENTS"
```

### 2. Semantic search (meaning-based, finds related concepts)
```bash
uv run --script "$PLUGIN_SCRIPTS/vsearch.py" "$ARGUMENTS"
```
Finds results even when exact words don't match. Supports filters: `--project X`, `--days N`, `--type message|session|message_user_query`, `--limit N`. Omitting `--type` searches full message text and sessions, not `message_user_query` (avoids duplicate hits per turn).

## Interpreting results

**Keyword** returns exact substring matches — high precision, low recall.
**Semantic** returns conceptually similar content — lower precision, high recall. Score > 0.7 = strong match, 0.5-0.7 = related.

Combine both to get a complete picture. Use session IDs to dig deeper with `cw:query`.

## Tips

- Search for error messages, library names, patterns, concepts
- Use short, specific terms for keyword search
- Use natural language descriptions for semantic search
- Combine with `cw:query` for complex lookups (e.g., "all sessions in project X that used tool Y")
