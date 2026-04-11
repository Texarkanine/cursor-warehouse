# Task: cursor-warehouse PR #1 Rework (Round 2)

* Task ID: visiona-pr-rework
* Complexity: Level 3
* Type: fix (rework from PR review feedback, round 2)

Address valid findings from second round of PR review feedback on [PR #1](https://github.com/Texarkanine/cursor-warehouse/pull/1). This is a low-complexity "direct port" — we want equivalent, not-incorrect functionality with no data omissions, not a new product.

## PR Feedback Triage (Round 2)

### VALID AND WORTH REWORKING

#### RW1. scripts/sync.py: workspace_slug fallback loses context (line 143)
**Source:** PR reviewer (inline)
**Justification:** Verified. `return parts[-1]` returns only the last hyphen-separated token (e.g., `"warehouse"` from `"s-Users-Austin-Documents-git-cursor-warehouse"`), which is ambiguous and causes collisions when multiple projects share a common trailing name.
**Approach (revised at preflight):** Greedy filesystem reconstruction. The slug encodes a real filesystem path with hyphens replacing separators. Walk parts left-to-right, testing `Path.is_dir()` for single parts first, then progressively longer hyphen-joined combinations (longest first) to handle directory names containing hyphens. The project name is the `.name` of the last matched directory when all parts resolve, or `parts[-1]` when reconstruction fails (deleted path, different machine — no worse than current behavior). Try `/` first (Linux/macOS), then `/mnt/<first-part>/` for WSL Windows-origin slugs. Reconstruction roots stored in a module-level list for testability (monkeypatchable). Culturally neutral — uses ground truth, not heuristics. All existing tests pass unchanged (synthetic slugs fall through to fallback).

#### RW2. scripts/sync.py: Model enrichment nondeterminism (lines 443-459)
**Source:** PR reviewer (inline)
**Justification:** Verified. `SELECT DISTINCT conversationId, model` without `ORDER BY` produces nondeterministic row ordering. The `UPDATE ... WHERE model IS NULL` means whichever `(conversation_id, model)` pair comes first in the result set wins — this varies across runs. Build a deterministic mapping (e.g., `min(model)` per conversation_id) before applying updates. 4-line change, no downside, removes real nondeterminism from the ETL layer.

#### RW3. scripts/sync.py: Missing UTF-8 encoding on file open (line 159)
**Source:** PR reviewer (inline)
**Justification:** Verified. `open(fp)` uses the platform default encoding. On native Windows (not WSL), the default is often `cp1252`, which would cause `UnicodeDecodeError` on valid UTF-8 Cursor JSONL files. Add `encoding='utf-8', errors='replace'`. One-line fix, prevents crash on Windows.

#### RW4. scripts/sync.py: _parse_tracking_timestamp misses ISO dates (lines 465-493)
**Source:** PR reviewer (inline)
**Justification:** Verified. The docstring claims to handle "ISO strings" but the implementation only tries epoch milliseconds → epoch string → git date (`parsedate_to_datetime`). `parsedate_to_datetime` parses RFC 2822, NOT ISO 8601. An ISO string like `"2026-04-10T12:00:00Z"` falls through to `return None`, silently dropping valid timestamps. Add `datetime.fromisoformat()` attempt before the `parsedate_to_datetime` fallback.

#### RW5. static/index.html: Date parsing causes one-day shift (lines 189, 197)
**Source:** PR reviewer (inline)
**Justification:** Verified. `new Date("YYYY-MM-DD")` parses as UTC midnight per ECMAScript spec. When displayed with `toLocaleDateString()` in US time zones (UTC-5 to UTC-8), this renders as the *previous* day. Both the daily chart (line 189) and weekly chart (line 197) have this bug. Fix: split the date string and construct a local Date via `new Date(year, monthIndex, day)`.

#### RW6. tests/test_sync.py: test_long_text_truncated uses shared directory (line 237)
**Source:** PR reviewer (inline)
**Justification:** Verified. `tmp = FIXTURES_DIR.parent / "tmp_long.jsonl"` writes to the shared `tests/` directory, which can collide in parallel pytest runs (`-n`) or fail on read-only checkouts. Change the test signature to accept pytest's `tmp_path` fixture and use `tmp = tmp_path / "tmp_long.jsonl"`. 2-line change, strictly better.

#### RW7. skills/{cw-report,cw-wrapped}/SKILL.md: Query examples bypass QUERY_SCRIPT
**Source:** PR reviewer (inline)
**Justification:** Verified. Both files define a `QUERY_SCRIPT` fallback resolution block (handling unset `CURSOR_PLUGIN_ROOT`), then instruct "use `uv run --script "$QUERY_SCRIPT" sql "..."` for all queries below." But every query block uses `${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "..."` directly, which fails when `CURSOR_PLUGIN_ROOT` is unset (e.g., dev/symlink install). The instruction and the examples contradict each other. Replace all direct invocations with `uv run --script "$QUERY_SCRIPT" sql "..."` for consistency.

### NOT VALID AND/OR NOT WORTH REWORKING

#### NRW1. dashboard.py: CORS wildcard Access-Control-Allow-Origin: * (line 257)
**Source:** PR reviewer (duplicate)
**Justification:** Already triaged as N7 in round 1 and rejected with the same rationale: localhost-only dashboard (`127.0.0.1:3141`) serving local data. Any process on localhost can already read the DuckDB file directly. Restricting CORS adds complexity with zero security benefit. The wildcard enables useful integrations (e.g., Cursor webview panels fetching from the dashboard API). Duplicate feedback — no change.

#### NRW2. vsearch.py: ANN paging for post-filter matches (lines 71-105)
**Source:** PR reviewer (duplicate)
**Justification:** Already triaged as N3 in round 1 and rejected: 3x oversampling (`LIMIT limit * 3`) before post-filtering by project/days is a standard ANN search pattern. Iterative paging adds significant complexity (offset tracking, loop termination, batch-size tuning) for marginal benefit in a local analytics tool where the typical result set is <100 rows. Pushing project/days predicates into the SQL would require JOINs that prevent HNSW index usage. Acceptable tradeoff for v0.1. Duplicate feedback — no change.

#### NRW3. static/index.html: Floating Chart.js version @4 (line 7)
**Source:** PR reviewer (nitpick)
**Justification:** `chart.js@4` auto-resolves to the latest 4.x release via jsDelivr CDN. Chart.js 4.x is stable with semver guarantees — minor/patch releases don't break APIs. For a localhost analytics dashboard, the convenience of auto-updates outweighs the negligible risk. Vendoring or pinning to e.g., `@4.3.0` adds maintenance burden (manual updates) for no practical benefit in a local tool. Not worth the churn for a direct port.

## Component Analysis

### Affected Components

- **`scripts/sync.py`**: 4 fixes (RW1 workspace_slug fallback, RW2 model determinism, RW3 UTF-8 encoding, RW4 ISO timestamp parsing)
- **`static/index.html`**: 1 fix (RW5 date parsing)
- **`tests/test_sync.py`**: 1 fix (RW6 tmp_path)
- **`skills/cw-report/SKILL.md`**: 1 fix (RW7 query script references)
- **`skills/cw-wrapped/SKILL.md`**: 1 fix (RW7 query script references)

### Cross-Module Dependencies

None — all fixes are isolated within their respective files. No cross-module interactions.

### Boundary Changes

None — no interface, API, or schema changes. All fixes are internal implementation corrections.

### Invariants & Constraints

1. All existing 59 tests must continue passing
2. No new external dependencies
3. Changes must be backward-compatible (no data format changes)

## Open Questions

None — all rework items have clear implementations.

## Test Plan (TDD)

### Behaviors to Verify

**RW1 (workspace_slug):**
- Slug whose path exists on disk → filesystem reconstruction recovers correct project name (including multi-hyphen names and non-ASCII paths)
- Slug whose path does NOT exist → falls back to `parts[-1]` (same as current behavior)
- WSL slug with drive letter prefix → tries `/mnt/<drive>/...` reconstruction
- Single-token slug → returns that token
- Empty slug → returns `""`
- Ephemeral workspace slug with "Workspaces" → existing behavior unchanged

**RW2 (model determinism):**
- Conversation with multiple models → deterministic model chosen (min)

**RW4 (ISO timestamp):**
- ISO 8601 string → parsed correctly (not dropped)
- ISO string without timezone → gets UTC timezone appended
- Existing epoch and git-date handling unchanged

### Test Infrastructure

- Framework: pytest
- Run command: `uv run --with pytest --with duckdb pytest tests/ -v`
- Test location: `tests/test_sync.py`
- New test files: none

### New/Modified Tests

- RW1: Add test for filesystem reconstruction with multi-hyphen directory (create real dirs under `tmp_path`, monkeypatch roots)
- RW1: Add test for reconstruction fallback when path doesn't exist (returns `parts[-1]`)
- RW1: Add test for WSL `/mnt/<drive>/` root attempt
- RW1: Existing `test_standard_slug` unchanged — still expects `"myproject"` ✅
- RW2: Add test for deterministic model selection with multi-model conversations
- RW4: Add test cases for ISO timestamp parsing in `_parse_tracking_timestamp`
- RW6: Modify `test_long_text_truncated` to accept `tmp_path`

### Integration Tests

Existing full sync integration tests cover the overall pipeline. No new integration tests needed.

## Implementation Plan

### Phase 1: Tests first (TDD stubs + implementation)

1. **Add/modify tests for RW1, RW2, RW4, RW6**
    - Files: `tests/test_sync.py`
    - Changes:
      - RW1: Add tests for filesystem reconstruction (real dirs under `tmp_path`, monkeypatch roots) and fallback
      - RW2: Add test that multiple models for one conversation produce deterministic result
      - RW4: Add test cases for ISO string parsing in `_parse_tracking_timestamp`
      - RW6: Change `test_long_text_truncated` to accept `tmp_path`, use `tmp_path / "tmp_long.jsonl"`

### Phase 2: Code fixes (sync.py)

2. **Fix workspace_slug fallback (RW1)**
    - Files: `scripts/sync.py`
    - Changes:
      - Add `_RECONSTRUCTION_ROOTS: list[Path]` module-level (default `[Path("/")]`)
      - Add `_reconstruct_project_name(parts, root)` helper: greedy left-to-right `is_dir()` walk, trying single parts then longest multi-part joins; returns directory `.name` on full match or `None` on failure
      - Replace line 143 fallback: try reconstruction against each root (native `/`, then WSL `/mnt/<drive>/` if first part is a single char); fall back to `parts[-1]` when all roots fail
      - Add `@functools.lru_cache` on `_derive_project_name` — many sessions share the same workspace slug, so cache avoids redundant filesystem walks during sync

3. **Fix model enrichment nondeterminism (RW2)**
    - Files: `scripts/sync.py`
    - Changes: Lines 443-459: Build a `dict` mapping `conversation_id → min(model)` from the query results, then iterate the dict for updates

4. **Fix UTF-8 encoding (RW3)**
    - Files: `scripts/sync.py`
    - Changes: Line 159: `open(fp)` → `open(fp, encoding='utf-8', errors='replace')`

5. **Fix ISO timestamp parsing (RW4) + lazy import cleanup**
    - Files: `scripts/sync.py`
    - Changes:
      - Move `from email.utils import parsedate_to_datetime` from line 488 (lazy import inside `_parse_tracking_timestamp`) to top-level imports
      - Insert `datetime.fromisoformat(val)` attempt (with UTC normalization for naive datetimes) before `parsedate_to_datetime` fallback

### Phase 3: Frontend fix

6. **Fix date parsing one-day shift (RW5)**
    - Files: `static/index.html`
    - Changes: Lines 189, 197: Replace `new Date(d.day)` / `new Date(d.week)` with local date construction from split parts

### Phase 4: Documentation fixes

7. **Fix SKILL.md query script references (RW7)**
    - Files: `skills/cw-report/SKILL.md`, `skills/cw-wrapped/SKILL.md`
    - Changes: Replace all `${CURSOR_PLUGIN_ROOT}/scripts/query.py sql "..."` with `uv run --script "$QUERY_SCRIPT" sql "..."`

### Phase 5: Verification

8. **Run full test suite**
    - `uv run --with pytest --with duckdb pytest tests/ -v`
    - All 59+ tests must pass

## Technology Validation

No new technology — all fixes use existing Python stdlib and JavaScript APIs.

## Challenges & Mitigations

- **RW4 (fromisoformat compatibility):** `datetime.fromisoformat()` in Python 3.11+ handles most ISO 8601 formats including the `Z` suffix. Since PEP 723 already specifies `requires-python = ">=3.11"`, this is safe.
- **RW2 (min model semantics):** `min(model)` picks the lexicographically smallest model string. This is arbitrary but deterministic, which is the goal. The display layer (R9) already uses `STRING_AGG(DISTINCT model, ', ')` for accurate multi-model display.

## Status

- [x] PR feedback triage complete (7 valid, 3 rejected)
- [x] Component analysis complete
- [x] Open questions resolved (none)
- [x] Test planning complete (TDD)
- [x] Implementation plan complete (8 steps across 5 phases)
- [x] Technology validation complete (no new tech)
- [x] Preflight (PASS — revised: container-dir heuristic for RW1, lazy import fix added to RW4)
- [x] Build
- [ ] QA
