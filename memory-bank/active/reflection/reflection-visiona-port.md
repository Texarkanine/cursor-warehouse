---
task_id: visiona-port
date: 2026-04-10
complexity_level: 3
---

# Reflection: cursor-warehouse VISIONA Port

## Summary

Successfully ported claude-warehouse to cursor-warehouse in a single build session. All 28 implementation steps completed, 43 tests passing, smoke test verified against 220 real Cursor agent sessions (7,627 messages, 1,641 tool calls). The port required a JSONL parser rewrite, Claude-specific feature removal, and novel `ai-code-tracking.db` integration — but the inherited architecture made the query layer, dashboard, and embeddings nearly mechanical adaptations.

## Requirements vs Outcome

Every requirement from the VISIONA spec and project brief was delivered:
- JSONL sync reads Cursor transcripts correctly (verified against 218 real files)
- `harness` column on all 5 provenance tables with `DEFAULT 'cursor'`
- 5 Claude-specific tables dropped, `scored_commits` table added
- CLI prog renamed to `cursor-warehouse`, `hooks` command removed
- Dashboard rebranded, cost sections repurposed as sessions-by-project
- Plugin manifest, hooks, 4 skills, LICENSE, README all shipped
- Tracking DB integration (model enrichment + scored_commits) implemented

No requirements were dropped. One minor addition not in the original plan: `api_ai_attribution()` endpoint in dashboard.py was added per the plan revision (Phase 2b), not the original plan.

## Plan Accuracy

The implementation plan was remarkably accurate:
- **Correct sequence**: The 6-phase ordering worked perfectly. Foundation → sync → tracking → query → embed → package.
- **Correct scope**: All 13 components identified in the plan were needed and sufficient.
- **Correct challenges**: The JSONL parser rewrite was indeed the main work. The format differences (no UUIDs, no timestamps, `role` vs `type`) were all anticipated correctly by the data investigation phase.
- **One surprise**: The ambiguous column reference in `query.py` (`session_id` in a JOIN) was not anticipated in the plan but was trivially fixed during smoke testing.
- **Plan revision paid off**: The Phase 2b addition (tracking DB integration) for `ai-code-tracking.db` was discovered during the plan phase through proactive data investigation. This wasn't in the upstream spec at all — it's a genuinely new Cursor-specific capability.

## Creative Phase Review

No creative phase was needed. The VISIONA spec was detailed enough that all design questions had clear answers. The data investigation that led to the plan revision (discovering `ai-code-tracking.db`) could be considered a mini-creative phase within planning, and it held up perfectly during implementation.

## Build & QA Observations

**What went well:**
- TDD cycle was clean: tests written first, all failed, implementation made them pass.
- The upstream code served as an excellent template. Adapting existing code is faster than writing from scratch.
- Smoke test against real data caught the `query.py` ambiguous column issue that unit tests couldn't surface (unit tests use simple queries, not JOINs).
- Exception handling for tracking DB gracefully handled both `OperationalError` and `DatabaseError` (the latter discovered during TDD when testing with a non-SQLite file).

**What was hard:**
- The tracking DB path resolution on WSL is a known limitation. `ai-code-tracking.db` lives at a Windows path that's not directly accessible from the WSL `~/.cursor` home. This was documented as a known limitation.

**What QA caught:**
- 5 trivial issues — lazy imports, dead variable, unused parameter, unused import. All YAGNI/DRY violations from iterative development. No substantive issues.

## Cross-Phase Analysis

- **Planning → Build**: The comprehensive data investigation during planning (examining 218 JSONL files, the tracking DB schema, and dead `chats/` format) eliminated virtually all unknowns before build started. Build was smooth because planning was thorough.
- **Preflight → Build**: Preflight caught the hooks file location issue (`hooks/hooks.json` vs `.cursor/hooks.json`) early. Without this, it would have been a confusing deployment issue.
- **Build → QA**: QA found only trivial issues, suggesting the TDD approach and careful upstream adaptation prevented substantive defects.

## Post-Build QA Rework (Manual Testing)

After the reflect phase, manual smoke testing of the installed plugin revealed three bugs that unit tests couldn't catch — all related to real-world data formats and environment-specific paths.

### Bugs Found

1. **WSL tracking DB discovery** (`_find_tracking_db`): Only searched `~/.cursor/ai-tracking/` but on WSL the tracking DB lives on the Windows side at `/mnt/<drive>/Users/<user>/.cursor/ai-tracking/`. Model enrichment and scored commits were silently skipped.
2. **Cursor ephemeral workspace naming** (`_derive_project_name`): Slugs like `s-Users-Austin-AppData-Roaming-Cursor-Workspaces-1764355524551-workspace-json` produced project name "json" (48 sessions all named "json"). The last-segment heuristic failed for Cursor's internal workspace format.
3. **Tracking DB timestamp formats** (`_sync_scored_commits`): `scoredAt` stored as epoch milliseconds (BIGINT), `commitDate` stored as git date strings (`Fri Feb 20 09:59:00 2026 -0600`). DuckDB couldn't cast either to TIMESTAMP, causing the entire scored_commits import to fail.

### Fixes Applied (TDD)

- `_find_tracking_db()` now searches `/mnt/*/Users/*/.cursor/ai-tracking/` as a WSL fallback, with native path preferred. Testable via `_wsl_mnt_root()` seam (5 new tests).
- `_derive_project_name()` detects `Workspaces` segment in slug and extracts the timestamp ID, producing `workspace-1764355524551` instead of `json` (3 new tests).
- New `_parse_tracking_timestamp()` helper handles epoch-ms (BIGINT), epoch-string, and git date format via `email.utils.parsedate_to_datetime`. Falls back to None for unparseable values.
- 53 tests passing (was 43), zero regressions.

### Plugin Installation Discovery

Testing also surfaced the correct local plugin installation workflow for Cursor:
- Cursor's GitHub plugin installer only pulls the **default branch** — can't test unmerged branches that way.
- Local testing requires symlinking to `~/.cursor/plugins/local/<name>/` **plus** registering in `~/.claude/plugins/installed_plugins.json` and `~/.claude/settings.json` (Cursor shares Claude Code's config surface for plugin discovery).
- Plugin `plugin.json` must declare component paths explicitly (`"skills": "./skills/"`, `"hooks": "./hooks/hooks.json"`) — without these, Cursor discovers the plugin but doesn't load its skills.
- Skill frontmatter requires a `name` field to match the official plugin format.
- README updated with both installation paths (GitHub URL for released code, local symlink+registration for branch testing).

### Post-Fix Verification

Fresh sync against real data: 173 sessions, 53 subagents, 157 model-conversation pairs, 326 scored commits. Model distribution: `claude-4.6-opus-high-thinking` (4846 msgs), `composer-2-fast` (966), `claude-4.6-sonnet-medium-thinking` (192), `grok-4-20-thinking` (173), `gpt-5.4-medium` (141), `gpt-5.3-codex` (95). Only 1194 "unknown" remaining (non-code-producing turns without tracking DB matches — expected).

## Insights

### Technical
- **Cursor JSONL is sparser than Claude Code**: No per-message UUIDs, timestamps, or tokens. The synthetic UUID scheme (`{session_id}:{line_idx}`) works well for dedup and uniqueness but isn't semantically meaningful. Future work might want a content hash-based UUID for better cross-session dedup.
- **`ai-code-tracking.db` is the richest Cursor-specific data source**: It provides model info, per-request timestamps, and commit-level AI attribution — none of which exist in the JSONL. Any future Cursor analytics tool should prioritize this database.
- **Positional INSERT is fragile**: The sessions and scored_commits INSERTs use positional `VALUES (?,?,...?)` without naming columns. If schema changes, these silently break. The messages/tool_calls INSERTs use named columns — this inconsistency should be unified in a future cleanup.
- **Real-world data formats are unpredictable**: The tracking DB stores timestamps as epoch-ms integers in one column and git date strings in another. Unit tests with synthetic data (ISO strings) passed fine; only real data exposed the mismatch. Always smoke test against production data.
- **WSL path bifurcation is a recurring Cursor issue**: The IDE server runs in WSL but desktop-side data (tracking DB, workspace storage) lives on Windows mount paths. Any Cursor tool that touches non-transcript data needs WSL-aware path resolution.

### Process
- **Proactive data investigation during planning is high-ROI**: The entire `ai-code-tracking.db` integration (a genuinely new feature not in the upstream) was discovered because the plan phase invested time examining actual Cursor data files. This added 5 implementation steps but resulted in a significantly more valuable product.
- **Port tasks are faster than they look**: The 28-step plan looked daunting but the inherited architecture meant most steps were mechanical adaptations. The only genuinely novel code was the JSONL parser and tracking DB integration (~200 lines out of ~1500 total).
- **Manual testing catches what unit tests can't**: All three rework bugs were environment-specific (WSL paths) or data-format-specific (epoch-ms, git dates). Unit tests with synthetic fixtures passed cleanly. The lesson: for data pipeline tools, always test against real production data before declaring done.
- **Plugin testing is its own category of work**: The Cursor plugin system is underdocumented for local development. Discovering the correct installation workflow (symlink + `~/.claude/` registration + explicit manifest paths + `name` frontmatter) required iterative trial-and-error. This knowledge should be captured in the README for future contributors.
