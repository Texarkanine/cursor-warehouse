# Task: cursor-warehouse PR #1 Rework

* Task ID: visiona-pr-rework
* Complexity: Level 3
* Type: fix (rework from PR review feedback)

Address valid findings from CodeRabbit and LlamaPReview automated reviews on [PR #1](https://github.com/Texarkanine/cursor-warehouse/pull/1). Feedback has been triaged into "valid and worth reworking" vs "not valid / not worth reworking" with justifications.

## PR Feedback Triage

### VALID AND WORTH REWORKING

#### R1. hooks/hooks.json: Remove `&` backgrounding from hook commands
**Source:** CodeRabbit (Major) + LlamaPReview (P1)
**Justification:** Cursor hooks run synchronously with managed timeouts. The `&` detaches the process immediately, breaking timeout enforcement, error handling, and enabling duplicate process spawning. `sync.py` completes quickly (<5s) and should run synchronously. `dashboard.py` has `serve_forever()` (blocks forever) so it can't run synchronously — it must be spawned from sync.py after sync completes (if not already running), not from the hook directly.

#### R2. scripts/sync.py: ON CONFLICT for scored_commits only updates 3 of 15 mutable fields
**Source:** CodeRabbit (Nitpick)
**Justification:** The upsert only updates `scored_at`, `lines_added`, `lines_deleted` on conflict. The other 12 fields (`tab_lines_added`, `tab_lines_deleted`, `composer_lines_added`, `composer_lines_deleted`, `human_lines_added`, `human_lines_deleted`, `blank_lines_added`, `blank_lines_deleted`, `commit_message`, `commit_date`, `v1_ai_percentage`, `v2_ai_percentage`) are silently dropped on re-sync. This means corrected historical data in the tracking DB won't propagate. Clear bug.

#### R3. static/index.html: XSS via innerHTML injection
**Source:** CodeRabbit (Critical)
**Justification:** `project_name`, `model`, `prompt`, and `dev_type` are data-derived fields injected unsanitized into `innerHTML`. While the attack surface is minimal (localhost dashboard, local data), this is a correctness issue and trivial to fix with an `esc()` helper. Good hygiene for a published plugin.

#### R4. scripts/embed.py: Double-prefixed source_id breaks vsearch enrichment
**Source:** CodeRabbit (Major)
**Justification:** Verified against code. `embed.py` builds source_id as `f"{sid}:{uuid}"` where `uuid` is already `session_id:line_idx` (from sync.py line 133). Result: `session_id:session_id:line_idx`. In vsearch.py `enrich()`, `source_id.split(":", 1)` extracts `sid` correctly, but then queries `m.uuid = source_id` (the full double-prefixed string), which won't match `messages.uuid` (which is `session_id:line_idx`). This breaks all vsearch enrichment metadata (project, date show as empty). Real bug.

#### R5. scripts/vsearch.py: Parameterized INTERVAL invalid DuckDB SQL
**Source:** CodeRabbit (Major)
**Justification:** Line 91: `INTERVAL ? DAY` — DuckDB requires interval literals, not parameters. This will throw a runtime error when `--days` is used. Fix: `current_date - (? * INTERVAL '1 day')`.

#### R6. static/index.html: Handle non-2xx fetch responses
**Source:** CodeRabbit (Nitpick)
**Justification:** `fetchJSON()` calls `r.json()` without checking `r.ok`. HTTP 500 errors from the dashboard API surface as opaque JSON parse errors instead of useful error messages. Trivial defensive fix.

#### R7. scripts/sync.py: Silent exception swallowing in _ingest_jsonl
**Source:** CodeRabbit (Nitpick) + LlamaPReview (P2)
**Justification:** Line 185: `except Exception: return None` silently swallows ALL errors including permission denied, I/O errors, encoding errors. A `print(... file=sys.stderr)` warning is trivial and dramatically improves debuggability for an ETL pipeline.

#### R8. scripts/sync.py: Include exception type in tracking DB failure message
**Source:** CodeRabbit (Nitpick)
**Justification:** Line 524: `except Exception as e:` should include `type(e).__name__` in the verbose message. The broad catch is intentional (maximum resilience), but logging the exception type costs nothing and helps diagnosis.

#### R9. dashboard.py + query.py: MAX(model) is non-deterministic for multi-model sessions
**Source:** CodeRabbit (Major) + LlamaPReview (P2)
**Justification:** `MAX(model)` picks the lexicographically largest model string, which is arbitrary and misleading for sessions that used multiple models. `STRING_AGG(DISTINCT model, ', ' ORDER BY model)` would be deterministic, accurate, and equally simple.

#### R10. skills/report/SKILL.md: Off-by-one bucket label
**Source:** CodeRabbit (Minor)
**Justification:** CASE expression: `message_count <= 30 → 'medium (11-30 msgs)'`, ELSE `'long (30+ msgs)'`. A 30-message session is medium, so ELSE is actually 31+. Label should be `'long (31+ msgs)'`. Genuine off-by-one.

#### R11. skills/{report,wrapped,query}/SKILL.md: Missing language tags on code fences
**Source:** CodeRabbit (Minor)
**Justification:** Three SKILL.md files have bare ``` fences for schema reference blocks. Adding `text` language tag satisfies markdownlint MD040 and is trivial.

#### R12. tests/test_sync.py: Unused variable prefixes
**Source:** CodeRabbit (Nitpick)
**Justification:** Three occurrences of unpacked-but-unused variables (`sid`, `subagents`). Prefixing with `_` signals intent. Trivial.

### NOT VALID AND/OR NOT WORTH REWORKING

#### N1. scripts/schema.sql: Include harness in composite primary keys
**Source:** CodeRabbit (Nitpick)
**Justification:** This is a single-harness system. The `harness` column exists as a forward-compatibility marker (documented in systemPatterns.md). Adding it to PKs would require cascading changes throughout ALL queries, ON CONFLICT clauses, and JOIN conditions for zero practical benefit — there is no second harness. Multi-harness support is explicitly VISIONB/C scope. The "collision" scenario requires a second harness emitting overlapping session UUIDs, which is astronomically unlikely even if multi-harness were implemented (UUIDs don't collide).

#### N2. static/index.html: Bucket color mapping "never matches"
**Source:** CodeRabbit (Minor)
**Justification:** **False positive.** CodeRabbit confused the report SKILL.md SQL (which uses descriptive labels like `'abandoned (1-3 msgs)'`) with the dashboard API SQL. The actual `dashboard.py` `api_efficiency()` returns bare labels: `'abandoned'`, `'short'`, `'medium'`, `'long'` — which perfectly match the `bucketColors` keys. No fix needed.

#### N3. scripts/vsearch.py: ANN LIMIT before filtering drops valid matches
**Source:** CodeRabbit (Major)
**Justification:** The 3x oversampling (`LIMIT limit * 3`) before post-filtering by project/days is a standard pattern for ANN search with post-filters. Adding project/days filters inside the SQL would require expensive JOINs that prevent HNSW index usage. Iterative paging adds significant complexity for marginal benefit in a local analytics tool. Acceptable tradeoff for v0.1.

#### N4. scripts/dashboard.py: Unused AI attribution endpoint
**Source:** LlamaPReview (P2)
**Justification:** Intentional. The `api_ai_attribution` endpoint was built to serve the API for skills and external consumers. The frontend chart is VISIONB scope. The endpoint is not dead code — it's a completed API surface that the frontend will consume later. Skills already reference `scored_commits` data.

#### N5. scripts/query.py: Inefficient model subquery performance
**Source:** LlamaPReview (P2)
**Justification:** Premature optimization. The dataset is local (typically <500 sessions). DuckDB handles this subquery efficiently via hash aggregation. Materializing model info into the sessions table during sync adds sync complexity and creates a second source of truth. Not worth the coupling for a local analytics tool.

#### N6. scripts/sync.py: Per-conversation model UPDATE round-trips
**Source:** LlamaPReview (P2)
**Justification:** The model enrichment loop (~150-200 iterations for a typical user) completes in <100ms on DuckDB. A batch UPDATE via temp table would be cleaner but adds code complexity for negligible performance gain. Not blocking for v0.1.

#### N7. scripts/dashboard.py: CORS wildcard Access-Control-Allow-Origin: *
**Source:** CodeRabbit (inline)
**Justification:** This is a localhost-only dashboard (`127.0.0.1:3141`) serving local data. Restricting CORS adds complexity with zero security benefit — any process on localhost can already read the DuckDB file directly. The wildcard enables useful integrations (e.g., a Cursor webview panel fetching from the dashboard API).

#### N8. memory-bank/: Various doc cleanup (reflection stale text, hooks naming, VISIONA language tags)
**Source:** CodeRabbit (various)
**Justification:** Memory-bank files are development process documents, not shipped code. The reflection accurately captured the state at writing time. The hooks naming "conflict" is already explained in context (hooks.json is the source, .cursor/hooks.json is the install target). VISIONA.md language tags are cosmetic. None affect functionality or user experience.

## Component Analysis

### Affected Components

- **`hooks/hooks.json`**: Hook configuration → remove `&` from sync, restructure dashboard startup
- **`scripts/sync.py`**: ETL pipeline → fix scored_commits upsert, add error logging, improve exception messages, spawn dashboard after sync
- **`scripts/embed.py`**: Embedding pipeline → fix double-prefixed source_id
- **`scripts/vsearch.py`**: Vector search → fix INTERVAL SQL syntax
- **`scripts/dashboard.py`**: HTTP dashboard → fix MAX(model) to deterministic summary
- **`scripts/query.py`**: CLI query → fix MAX(model) to deterministic summary
- **`static/index.html`**: Dashboard frontend → add XSS escaping, handle non-2xx responses
- **`skills/report/SKILL.md`**: Skill doc → fix off-by-one label, add language tag
- **`skills/wrapped/SKILL.md`**: Skill doc → add language tag
- **`skills/query/SKILL.md`**: Skill doc → add language tag
- **`tests/test_sync.py`**: Tests → prefix unused variables

### Cross-Module Dependencies

- `embed.py` source_id fix (R4) must match `vsearch.py` enrichment logic
- `embed.py` source_id fix (R4) must match `count_unembedded()` and `clean_stale_embeddings()` queries
- Dashboard startup change (R1) requires `sync.py` to spawn `dashboard.py`

### Boundary Changes

- `embed.py` embeddings.source_id format changes from `session_id:session_id:line_idx` to `session_id:line_idx` — existing embeddings will become stale and be cleaned up on next `embed.py --full` run
- Hook commands change — users with existing plugin installs will get new behavior on update

### Invariants & Constraints

1. All existing 53 tests must continue passing
2. No new dependencies
3. Backward compatibility: existing embeddings cleaned gracefully (not crash)
4. Hook changes must not break non-plugin installs

## Open Questions

None — all rework items have clear implementations.

## Test Plan (TDD)

### Behaviors to Verify

**R2 (scored_commits upsert):**
- Re-syncing scored_commits with changed tab/human/AI% values → all fields update
- Existing test `test_scored_commits_dedup` may need enhancement

**R4 (embed source_id):**
- Message embedding source_id matches vsearch enrichment lookup
- Stale embeddings with old double-prefixed IDs are cleaned on next run

**R5 (vsearch INTERVAL):**
- `--days` filter works without SQL error

**R7 (ingest error logging):**
- Malformed file produces stderr warning (not silent skip)

### Test Infrastructure

- Framework: pytest
- Run command: `uv run --with pytest --with duckdb pytest tests/ -v`
- Test location: `tests/`
- Modified test files: `tests/test_sync.py` (existing tests + R2 enhancement)
- No new test files needed — most fixes are in UI/query layer not covered by unit tests

### Integration Tests

- Existing full sync integration tests cover R2 (scored_commits)
- R4 requires manual verification (embed.py + vsearch.py interaction)
- R5 requires manual verification (vsearch.py with --days flag)

## Implementation Plan

### Phase 1: Bug fixes (scripts)

1. **Fix scored_commits upsert to update ALL mutable fields (R2)**
    - Files: `scripts/sync.py`
    - Changes: Expand ON CONFLICT DO UPDATE SET to include all 12 additional mutable columns
    - TDD: Enhance `test_scored_commits_dedup` to verify all fields update on re-sync

2. **Fix embed.py double-prefixed source_id (R4)**
    - Files: `scripts/embed.py`
    - Changes: In `embed_messages()` and `count_unembedded()` and `clean_stale_embeddings()`, change source_id from `f"{sid}:{uuid}"` to just `uuid` (since uuid already contains session_id prefix). Update all `m.session_id || ':' || m.uuid` SQL concatenations to just `m.uuid`.

3. **Fix vsearch.py parameterized INTERVAL (R5)**
    - Files: `scripts/vsearch.py`
    - Changes: Line 91: change `INTERVAL ? DAY` to `? * INTERVAL '1 day'`

4. **Fix MAX(model) to deterministic summary (R9)**
    - Files: `scripts/dashboard.py`, `scripts/query.py`
    - Changes: Replace `MAX(model)` with `STRING_AGG(DISTINCT model, ', ' ORDER BY model)` in both files' model subqueries

5. **Add error logging to _ingest_jsonl (R7)**
    - Files: `scripts/sync.py`
    - Changes: Line 185: `except Exception as e: print(f"[sync] Skipping {fp}: {type(e).__name__}: {e}", file=sys.stderr); return None`

6. **Include exception type in tracking DB failure message (R8)**
    - Files: `scripts/sync.py`
    - Changes: Line 526: add `type(e).__name__` to the verbose message

### Phase 2: Frontend fixes

7. **Add XSS escaping to innerHTML injections (R3)**
    - Files: `static/index.html`
    - Changes: Add `esc()` function that escapes `&`, `<`, `>`, `"`, `'`. Apply to: `project_name`, `model`, `prompt` in sessions table; `dev_type`, `top_tool`, `project_name` in wrapped section.

8. **Handle non-2xx fetch responses (R6)**
    - Files: `static/index.html`
    - Changes: Add `if (!r.ok)` check in `fetchJSON()` before calling `r.json()`

### Phase 3: Hook architecture fix

9. **Remove `&` from sync.py hook, restructure dashboard startup (R1)**
    - Files: `hooks/hooks.json`, `scripts/sync.py`
    - Changes:
      - `hooks/hooks.json`: Remove both hook entries. Replace with single sync.py hook (no `&`, timeout 30000ms)
      - `scripts/sync.py`: After sync completes in `main()`, spawn `dashboard.py` in background via `subprocess.Popen` if port 3141 is not in use (import socket, check port, Popen with stdout/stderr redirected to /dev/null)

### Phase 4: Documentation & lint cleanup

10. **Fix off-by-one bucket label (R10)**
    - Files: `skills/report/SKILL.md`
    - Changes: `'long (30+ msgs)'` → `'long (31+ msgs)'`

11. **Add language tags to code fences (R11)**
    - Files: `skills/report/SKILL.md`, `skills/wrapped/SKILL.md`, `skills/query/SKILL.md`
    - Changes: Add `text` to bare ``` fences for schema reference blocks

12. **Prefix unused test variables (R12)**
    - Files: `tests/test_sync.py`
    - Changes: `sid` → `_sid` (lines 119, 203), `subagents` → `_subagents` (line 314)

### Phase 5: Verification

13. **Run full test suite**
    - `uv run --with pytest --with duckdb pytest tests/ -v`
    - All 53+ tests must pass

14. **Manual smoke tests**
    - `uv run --script scripts/sync.py -v` — verify error logging on any malformed files
    - Verify vsearch.py with `--days` flag doesn't crash (R5)

## Technology Validation

No new technology — all fixes use existing dependencies and patterns.

## Challenges & Mitigations

- **R4 (embed source_id) breaks existing embeddings**: Existing embeddings in the DB have double-prefixed source_ids. `clean_stale_embeddings()` will detect them as stale (no matching message) and clean them on next `embed.py` run. New embeddings will use correct single-prefix IDs. No migration needed — the stale cleanup mechanism handles it. Document in commit message.
- **R1 (dashboard spawn from sync)**: The subprocess.Popen in sync.py must be fire-and-forget (no wait). Must handle case where sync.py is run manually without dashboard.py available. Use try/except around the Popen.
- **R9 (STRING_AGG model)**: Changing from `MAX(model)` to `STRING_AGG(DISTINCT model, ', ')` may produce longer strings for multi-model sessions. The dashboard and CLI already truncate model display, so this is safe.

## Status

- [x] PR feedback triage complete (12 valid, 8 rejected)
- [x] Component analysis complete
- [x] Open questions resolved (none)
- [x] Test planning complete (TDD)
- [x] Implementation plan complete (14 steps across 5 phases)
- [x] Technology validation complete (no new tech)
- [ ] Preflight
- [ ] Build
- [ ] QA
