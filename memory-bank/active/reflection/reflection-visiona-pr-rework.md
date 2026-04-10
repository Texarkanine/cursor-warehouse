---
task_id: visiona-pr-rework
date: 2026-04-10
complexity_level: 3
---

# Reflection: cursor-warehouse PR #1 Rework

## Summary

All 14 valid PR review findings were addressed across 6 build phases: 3 bugs fixed, 2 security improvements, 1 architectural change (fork-and-return hooks), 4 lint/hygiene items, and 2 new features (skill namespace, initialize skill). QA caught one bug in the new hook launcher. 59 tests passing (7 new).

## Requirements vs Outcome

Every rework item was implemented exactly as planned. No items were dropped, descoped, or reinterpreted. No additional work was added beyond the 14 planned items. The 8 rejected items were correctly excluded.

## Plan Accuracy

The 16-step, 6-phase plan was executed in exact order with no reordering, splitting, or additions needed. The plan correctly identified that R4 (embed source_id) would be the most cross-cutting fix and that existing embeddings would be gracefully cleaned. The plan correctly identified that R1 (hook architecture) needed a new launcher script.

The one gap: the plan specified the detachment mechanism for hook-launcher.py (`start_new_session=True` on POSIX, `DETACHED_PROCESS` on Windows) but didn't specify how to invoke child scripts. The omission of "invoke via `uv run --script`" was caught by QA.

## Creative Phase Review

No creative phase was executed — all rework items had clear implementations from the plan phase.

## Build & QA Observations

Build was clean and sequential. TDD was effective: the R2 test detected the 3-of-15 field bug immediately (tab_lines_added stayed at 30 instead of updating to 60), and the R7 test confirmed the silent exception swallow. R4/R5 tests verified the correct SQL patterns as contract tests since embed.py and vsearch.py have heavy dependencies (sentence-transformers, torch) that can't be imported in the test environment.

QA caught one real bug: hook-launcher.py used `sys.executable` to spawn child scripts, but PEP 723 scripts need `uv run --script` to resolve their declared dependencies. With bare Python, sync.py would fail with `ModuleNotFoundError: No module named 'duckdb'` at runtime. This was a trivial fix but would have been a production blocker.

## Cross-Phase Analysis

The plan's thoroughness meant build had zero surprises. Preflight's two advisory items (format-contract comment on msg_uuid, unit tests for R4/R5) were both implemented during build. The QA finding (sys.executable vs uv) traces back to a plan-level gap: the plan focused on process detachment but not on process invocation. The hook launcher is the first script in this codebase that spawns other PEP 723 scripts as children, creating a novel interaction pattern not covered by existing conventions.

## Insights

### Technical

- **PEP 723 child spawning invariant**: Scripts that spawn other PEP 723 scripts must use `uv run --script`, not `sys.executable`. When uv creates an ephemeral environment, `sys.executable` resolves to that env's Python, which doesn't have other scripts' dependencies. This is a non-obvious consequence of the PEP 723 architecture that only surfaces when scripts orchestrate each other.

- **DuckDB parameterized INTERVAL**: `INTERVAL ? DAY` doesn't work in DuckDB — the INTERVAL keyword requires a literal. Use `? * INTERVAL '1 day'` for parameterized day offsets. Not well-documented; easy to miss.

### Process

- **Contract tests for cross-module SQL patterns** work well when the target module can't be imported in tests (heavy deps). Testing the SQL algebra independently against a real DuckDB confirms correctness without needing the full module. Applied to R4 (embed source_id ↔ messages.uuid) and R5 (INTERVAL syntax).

- **QA semantic review catches invocation bugs** that unit tests and integration tests miss. The hook-launcher had no unit tests (integration-tested only), and the sys.executable bug would only fail at runtime in a Cursor hook context. Semantic review asked "does this invocation actually resolve dependencies?" — a question no automated test was designed to answer.
