# Active Context

## Current Task

cursor-warehouse PR #1 Rework

## Phase

BUILD - COMPLETE

## What Was Done

- **Phase 1 (Bug fixes):** Fixed scored_commits upsert to update all 15 mutable fields (R2). Fixed embed.py double-prefixed source_id (R4). Fixed vsearch.py INTERVAL SQL syntax (R5). Fixed MAX(model) to deterministic STRING_AGG in dashboard.py + query.py (R9). Added error logging to _ingest_jsonl (R7). Added exception type to tracking DB failure message (R8).
- **Phase 2 (Frontend):** Added `esc()` XSS helper applied to all data-derived innerHTML injections (R3). Added `!r.ok` guard in fetchJSON (R6).
- **Phase 3 (Hook architecture):** Created hook-launcher.py — thin fork-and-return launcher using subprocess.Popen with start_new_session/DETACHED_PROCESS (R1). Updated hooks.json to single launcher entry.
- **Phase 4 (Skill namespace):** Renamed 4 skill directories to `cw-*` prefix with `cw:` name frontmatter (R13). Applied R10 off-by-one fix and R11 language tags during rename. Created new `cw:initialize` skill for first-time setup (R14). Updated README and cross-references.
- **Phase 5 (Code hygiene):** Prefixed 3 unused test variables with `_` (R12).
- **Phase 6 (Verification):** All 59 tests passing (9 schema + 50 sync, including 7 new). No linter errors.

## Key Decisions

- embed.py source_id now uses `m.uuid` directly (uuid is already `{session_id}:{line_idx}`) — eliminates double-prefixing
- hook-launcher.py uses `sys.executable` (the Python interpreter uv provided) to spawn children, not `uv run --script`
- Skill names use colons in frontmatter (`cw:query`) and hyphens in directories (`cw-query/`)

## Deviations from Plan

None — built to plan.

## Next Step

QA review will now run automatically.
