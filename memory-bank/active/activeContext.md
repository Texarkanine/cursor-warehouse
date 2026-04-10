# Active Context

## Current Task

cursor-warehouse PR #1 Rework

## Phase

PLAN - COMPLETE (revised with hook architecture + skill namespace)

## What Was Done

- Fetched all PR feedback from GitHub PR #1 (CodeRabbit: 16 actionable + 8 nitpick, LlamaPReview: 5 items)
- Read and verified every finding against the actual source code
- Categorized into 14 valid/rework items and 8 rejected items with detailed justifications
- Investigated sync timing problem: initial sync 10-30s, too slow for synchronous hooks
- Investigated background process options: fork-and-return via `subprocess.Popen(start_new_session=True)` confirmed viable
- Discovered upstream claude-warehouse uses launchd cron for sync, NOT hooks — validates our approach
- Created rework implementation plan: 16 steps across 6 phases

## Key Decisions

- **Hook architecture**: Fork-and-return via `hook-launcher.py` — forks sync.py + dashboard.py as fully detached processes, returns immediately. Replaces `&` backgrounding.
- **Skill namespace**: `cw:` prefix on all skill names (`cw:query`, `cw:recall`, `cw:report`, `cw:wrapped`). Directories use hyphens (`cw-query/`).
- **New `cw:initialize` skill**: First-time setup runs interactive `sync.py --full --verbose` with progress feedback.
- **N2 rejected (bucket colors)**: CodeRabbit's finding was a false positive — dashboard.py returns bare labels that match the JS color map keys
- **N1 rejected (harness in PKs)**: Over-engineering for zero benefit — single-harness system, multi-harness is VISIONB scope
- **R4 confirmed (embed source_id)**: Real bug — double-prefixed source_id breaks vsearch enrichment metadata

## Next Step

Proceed to Preflight phase to validate the rework plan.
