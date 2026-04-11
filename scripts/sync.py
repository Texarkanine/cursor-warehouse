#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.2"]
# ///
"""cursor-warehouse: incremental ETL from Cursor agent transcripts into DuckDB."""

import argparse
import functools
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import duckdb

CURSOR_DIR = Path.home() / ".cursor"

# Roots for workspace-slug → filesystem path reconstruction (monkeypatch in tests).
_RECONSTRUCTION_ROOTS: list[Path] = [Path("/")]
DB_PATH = CURSOR_DIR / "cursor-warehouse.duckdb"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db(con: duckdb.DuckDBPyConnection):
    con.execute(SCHEMA_PATH.read_text())


def get_watermark(con: duckdb.DuckDBPyConnection, source: str) -> float:
    r = con.execute(
        "SELECT last_mtime FROM _sync_state WHERE source_name = ?", [source]
    ).fetchone()
    return r[0] if r else 0.0


def set_watermark(con: duckdb.DuckDBPyConnection, source: str, mtime: float, files: int, rows: int):
    con.execute("""
        INSERT INTO _sync_state (source_name, last_mtime, last_run, files_synced, rows_synced)
        VALUES (?, ?, current_timestamp, ?, ?)
        ON CONFLICT (source_name)
        DO UPDATE SET last_mtime = excluded.last_mtime,
                      last_run = excluded.last_run,
                      files_synced = _sync_state.files_synced + excluded.files_synced,
                      rows_synced = _sync_state.rows_synced + excluded.rows_synced
    """, [source, mtime, files, rows])


def truncate(s: str | None, maxlen: int = 500) -> str | None:
    if s is None:
        return None
    return s[:maxlen] if len(s) > maxlen else s


# ---------------------------------------------------------------------------
# Sessions + Messages + Tool Calls (main + subagent JSONL)
# ---------------------------------------------------------------------------

def extract_text_content(content_blocks) -> str | None:
    """Extract concatenated text from content blocks (list of dicts/strings or a plain string)."""
    if isinstance(content_blocks, str):
        return content_blocks
    if not isinstance(content_blocks, list):
        return None
    texts = []
    for block in content_blocks:
        if isinstance(block, dict):
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
        elif isinstance(block, str):
            texts.append(block)
    return "\n".join(texts) if texts else None


_RE_USER_QUERY = re.compile(r"<user_query>\s*([\s\S]*)\s*</user_query>")
_RE_SLASH_CMD = re.compile(r"(?:^|\n)(/\S+[^\n]*)", re.MULTILINE)
_RE_ANY_XML_TAG = re.compile(r"<[a-z_]+[ >]")


def extract_user_query(raw_text: str | None) -> str | None:
    """Extract the actual user intent from a Cursor message, stripping system context.

    Cursor wraps user messages in XML system context (<user_query>, <rules>,
    <manually_attached_skills>, etc.).  This function extracts just the user's
    words using a priority chain:
      1. Content inside <user_query>...</user_query> tags
      2. A /slash-command invocation outside XML tags
      3. The full text if it contains no XML system context at all
      4. None if no user intent can be isolated
    """
    if not raw_text:
        return None

    m = _RE_USER_QUERY.search(raw_text)
    if m:
        extracted = m.group(1).strip()
        return extracted or None

    m = _RE_SLASH_CMD.search(raw_text)
    if m:
        return m.group(1).strip() or None

    if not _RE_ANY_XML_TAG.search(raw_text):
        return raw_text.strip() or None

    return None


def extract_first_prompt(content) -> str | None:
    """Extract the first user prompt text, truncated to 1000 chars."""
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return truncate(block.get("text", ""), 1000)
            elif isinstance(block, str):
                return truncate(block, 1000)
    elif isinstance(content, str):
        return truncate(content, 1000)
    return None


def _reconstruct_project_name(parts: list[str], root: Path) -> str | None:
    """Map hyphen-split slug segments to a real directory under root; return its name.

    At each step, prefer a single segment as the next directory name; if that path is
    not a directory, try multi-segment names (longest first) so names with hyphens
    resolve correctly. Returns None if not all segments can be matched.
    """
    if not parts:
        return ""
    root = root.resolve()
    i = 0
    current = root
    while i < len(parts):
        remaining = len(parts) - i
        found = False
        if remaining >= 1:
            cand = current / parts[i]
            if cand.is_dir():
                current = cand
                i += 1
                found = True
        if not found:
            for k in range(remaining, 1, -1):
                name = "-".join(parts[i : i + k])
                cand = current / name
                if cand.is_dir():
                    current = cand
                    i += k
                    found = True
                    break
        if not found:
            return None
    return current.name


def _candidate_reconstruction_roots(parts: list[str]) -> list[tuple[Path, list[str]]]:
    """Return (root, parts_slice) pairs to try for filesystem reconstruction."""
    out: list[tuple[Path, list[str]]] = [(r, parts) for r in _RECONSTRUCTION_ROOTS]
    if len(parts) >= 2 and len(parts[0]) == 1 and parts[0].isalpha():
        drive = parts[0].lower()
        out.append((_wsl_mnt_root() / drive, parts[1:]))
    return out


@functools.lru_cache(maxsize=4096)
def _derive_project_name(workspace_slug: str) -> str:
    """Derive a human-readable project name from a Cursor workspace slug.

    Workspace slugs encode paths with hyphens, e.g.
    'home-mobaxterm-Documents-git-myproject' -> 'myproject'

    When the encoded path still exists on disk, greedy directory matching recovers
    the true final directory name (including multi-hyphen names).

    Cursor ephemeral workspaces have slugs like:
    's-Users-Austin-AppData-Roaming-Cursor-Workspaces-1764355524551-workspace-json'
    For these, we extract the timestamp to produce 'workspace-1764355524551'.
    """
    if not workspace_slug:
        return ""
    parts = workspace_slug.split("-")

    # Detect Cursor ephemeral workspace slugs: contain "Workspaces" segment
    # followed by a numeric timestamp, then "workspace", then "json"
    try:
        ws_idx = parts.index("Workspaces")
        if ws_idx + 1 < len(parts) and parts[ws_idx + 1].isdigit():
            return f"workspace-{parts[ws_idx + 1]}"
    except ValueError:
        pass

    for root, pslice in _candidate_reconstruction_roots(parts):
        if not pslice:
            continue
        resolved = _reconstruct_project_name(pslice, root)
        if resolved is not None:
            return resolved

    return parts[-1] if parts else workspace_slug


def _ingest_jsonl(con: duckdb.DuckDBPyConnection, fp: Path, session_id: str,
                  is_subagent: bool = False, parent_session_id: str | None = None):
    """Parse a single Cursor JSONL file into sessions/messages/tool_calls.

    Returns (session_id, msg_count) or None if no messages found.
    """
    messages = []
    tool_calls_batch = []
    first_user_prompt = None
    tools_seen = set()
    models_seen = set()

    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            for line_idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Format contract: embed.py source_id and vsearch.py enrichment
                # both rely on uuid being '{session_id}:{line_idx}'.
                msg_uuid = f"{session_id}:{line_idx}"
                msg_payload = rec.get("message", {})
                if not isinstance(msg_payload, dict):
                    continue

                role = rec.get("role") or msg_payload.get("role")
                msg_type = role or ""
                content = msg_payload.get("content", [])
                model = msg_payload.get("model")

                if model:
                    models_seen.add(model)

                content_types = []
                tool_name = None
                text_content = None

                if isinstance(content, list):
                    for i, block in enumerate(content):
                        if not isinstance(block, dict):
                            continue
                        bt = block.get("type", "")
                        if bt and bt not in content_types:
                            content_types.append(bt)

                        if bt == "tool_use":
                            tn = block.get("name", "")
                            tools_seen.add(tn)
                            if not tool_name:
                                tool_name = tn

                            tool_calls_batch.append((
                                session_id, msg_uuid, i,
                                tn,
                                truncate(json.dumps(block.get("input", {}), default=str), 500),
                                None,  # timestamp
                            ))

                text_content = extract_text_content(content)

                user_query = None
                if msg_type == "user" and text_content:
                    user_query = truncate(extract_user_query(text_content), 2000)

                if msg_type == "user" and first_user_prompt is None:
                    first_user_prompt = extract_first_prompt(content)

                messages.append((
                    session_id, msg_uuid,
                    msg_type,
                    None,  # timestamp
                    role, model,
                    json.dumps(content_types) if content_types else None,
                    tool_name,
                    truncate(text_content, 2000),
                    user_query,
                ))
    except Exception as e:
        print(f"[sync] Skipping {fp}: {type(e).__name__}: {e}", file=sys.stderr)
        return None

    if not messages:
        return None

    file_mtime = fp.stat().st_mtime
    file_ts = datetime.fromtimestamp(file_mtime, tz=timezone.utc)

    # Derive project info from path structure
    # Path: .../projects/<workspace-slug>/agent-transcripts/<session-id>/<session-id>.jsonl
    project_path = str(fp.parent)
    workspace_slug = ""
    parts = fp.parts
    for i, part in enumerate(parts):
        if part == "agent-transcripts" and i > 0:
            workspace_slug = parts[i - 1]
            break
    project_name = _derive_project_name(workspace_slug)

    con.execute("""
        INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (session_id) DO UPDATE SET
            modified_at = excluded.modified_at,
            message_count = excluded.message_count,
            tools_used = excluded.tools_used,
            models_used = excluded.models_used,
            first_prompt = excluded.first_prompt
    """, [
        session_id, "cursor",
        project_path, project_name,
        file_ts.isoformat(), file_ts.isoformat(),
        len(messages),
        json.dumps(sorted(tools_seen)),
        json.dumps(sorted(models_seen)),
        first_user_prompt, str(fp),
        is_subagent, parent_session_id,
    ])

    # Dedup: drop embeddings tied to this session (message UUIDs still resolvable), then rows
    con.execute(
        "DELETE FROM embeddings WHERE source_type = 'session' AND source_id = ?",
        [session_id],
    )
    con.execute("""
        DELETE FROM embeddings
        WHERE source_type IN ('message', 'message_user_query')
          AND source_id IN (SELECT uuid FROM messages WHERE session_id = ?)
    """, [session_id])
    con.execute("DELETE FROM messages WHERE session_id = ?", [session_id])
    con.execute("DELETE FROM tool_calls WHERE session_id = ?", [session_id])

    if messages:
        deduped = {(m[0], m[1]): m for m in messages}
        con.executemany(
            "INSERT INTO messages (session_id, uuid, type, timestamp, role, model, "
            "content_types, tool_name, text_content, user_query) VALUES (?,?,?,?,?,?,?,?,?,?)",
            list(deduped.values()),
        )
    if tool_calls_batch:
        deduped_tc = {(t[0], t[1], t[2]): t for t in tool_calls_batch}
        con.executemany(
            "INSERT INTO tool_calls (session_id, message_uuid, idx, tool_name, tool_input, timestamp) "
            "VALUES (?,?,?,?,?,?)",
            list(deduped_tc.values()),
        )

    return session_id, len(messages)


def _scan_jsonl_files(projects_dir: Path, session_wm: float, subagent_wm: float):
    """Single rglob scan partitioned into sessions and subagents."""
    sessions = []
    subagents = []

    if not projects_dir.exists():
        return sessions, subagents

    for ws_dir in projects_dir.iterdir():
        if not ws_dir.is_dir():
            continue
        transcripts_dir = ws_dir / "agent-transcripts"
        if not transcripts_dir.exists():
            continue

        for p in transcripts_dir.rglob("*.jsonl"):
            if not p.is_file():
                continue
            mtime = p.stat().st_mtime
            is_sub = "/subagents/" in str(p) or "\\subagents\\" in str(p)
            if is_sub and mtime > subagent_wm:
                subagents.append((mtime, p))
            elif not is_sub and mtime > session_wm:
                sessions.append((mtime, p))

    sessions.sort(key=lambda x: x[0])
    subagents.sort(key=lambda x: x[0])
    return sessions, subagents


def sync_sessions(con: duckdb.DuckDBPyConnection, session_files: list[tuple[float, Path]],
                  verbose: bool = False):
    wm = get_watermark(con, "sessions")

    if verbose:
        print(f"  Sessions: {len(session_files)} files to process")

    max_mtime = wm
    total_rows = 0

    for mtime, fp in session_files:
        max_mtime = max(max_mtime, mtime)
        session_id = fp.stem
        result = _ingest_jsonl(con, fp, session_id=session_id, is_subagent=False)
        if result:
            total_rows += result[1]

    set_watermark(con, "sessions", max_mtime, len(session_files), total_rows)


def sync_subagents(con: duckdb.DuckDBPyConnection, subagent_files: list[tuple[float, Path]],
                   verbose: bool = False):
    if verbose:
        print(f"  Subagents: {len(subagent_files)} files to process")

    wm = get_watermark(con, "subagents")
    max_mtime = wm
    total_rows = 0

    for mtime, fp in subagent_files:
        max_mtime = max(max_mtime, mtime)
        subagent_id = fp.stem

        # Derive parent session ID from path structure
        # .../agent-transcripts/<parent-id>/subagents/<subagent>.jsonl
        parent_dir = fp.parent.parent
        parent_sid = parent_dir.name if parent_dir.name != "subagents" else None

        result = _ingest_jsonl(
            con, fp, session_id=subagent_id,
            is_subagent=True, parent_session_id=parent_sid,
        )
        if result:
            total_rows += result[1]

    set_watermark(con, "subagents", max_mtime, len(subagent_files), total_rows)


def sync_all(con: duckdb.DuckDBPyConnection, projects_dir: Path, verbose: bool = False):
    """Run the full sync pipeline against a projects directory."""
    session_wm = get_watermark(con, "sessions")
    subagent_wm = get_watermark(con, "subagents")
    session_files, subagent_files = _scan_jsonl_files(projects_dir, session_wm, subagent_wm)

    sync_sessions(con, session_files, verbose=verbose)
    sync_subagents(con, subagent_files, verbose=verbose)


# ---------------------------------------------------------------------------
# Tracking DB Integration (ai-code-tracking.db)
# ---------------------------------------------------------------------------

def _wsl_mnt_root() -> Path:
    """Return the WSL mount root for Windows drives (typically /mnt/)."""
    return Path("/mnt")


def _find_tracking_db() -> Path | None:
    """Discover ai-code-tracking.db from known locations.

    Searches the native ~/.cursor/ path first, then falls back to WSL
    Windows mount paths (/mnt/<drive>/Users/<user>/.cursor/ai-tracking/).
    """
    native = CURSOR_DIR / "ai-tracking" / "ai-code-tracking.db"
    if native.exists():
        return native

    # WSL fallback: search /mnt/<drive>/Users/*/.cursor/ai-tracking/
    mnt = _wsl_mnt_root()
    if mnt.is_dir():
        for drive in mnt.iterdir():
            if not drive.is_dir() or len(drive.name) != 1:
                continue
            users_dir = drive / "Users"
            if not users_dir.is_dir():
                continue
            for user_dir in users_dir.iterdir():
                candidate = user_dir / ".cursor" / "ai-tracking" / "ai-code-tracking.db"
                if candidate.exists():
                    return candidate
    return None


def sync_tracking_db(con: duckdb.DuckDBPyConnection, tracking_db_path: Path | None = None,
                     verbose: bool = False):
    """Read ai-code-tracking.db to populate messages.model and scored_commits.

    Gracefully skips if the tracking DB is missing or locked.
    """
    if tracking_db_path is None:
        tracking_db_path = _find_tracking_db()
    if tracking_db_path is None or not tracking_db_path.exists():
        if verbose:
            print("  Tracking DB not found — skipping model enrichment and scored_commits")
        return

    try:
        tracking_con = sqlite3.connect(f"file:{tracking_db_path}?immutable=1", uri=True)
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        if verbose:
            print(f"  Tracking DB locked or inaccessible: {e}")
        return

    try:
        _sync_model_from_tracking(con, tracking_con, verbose)
        _sync_scored_commits(con, tracking_con, verbose)
    finally:
        tracking_con.close()


def _sync_model_from_tracking(con: duckdb.DuckDBPyConnection, tracking_con, verbose: bool = False):
    """Populate messages.model via ai_code_hashes join on conversationId."""
    try:
        rows = tracking_con.execute(
            "SELECT DISTINCT conversationId, model FROM ai_code_hashes "
            "WHERE conversationId IS NOT NULL AND model IS NOT NULL"
        ).fetchall()
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        if verbose:
            print("  ai_code_hashes table not found or tracking DB corrupt")
        return

    if not rows:
        return

    model_by_conv: dict[str, str] = {}
    for conversation_id, model in rows:
        if conversation_id is None or model is None:
            continue
        prev = model_by_conv.get(conversation_id)
        model_by_conv[conversation_id] = model if prev is None else min(prev, model)

    for conversation_id, model in model_by_conv.items():
        con.execute(
            "UPDATE messages SET model = ? WHERE session_id = ? AND model IS NULL",
            [model, conversation_id],
        )

    if verbose:
        print(f"  Model enrichment: {len(model_by_conv)} conversations from tracking DB")


def _parse_tracking_timestamp(val) -> str | None:
    """Convert a tracking DB timestamp to ISO 8601 for DuckDB.

    Handles epoch milliseconds (BIGINT), git date strings
    ('Fri Feb 20 09:59:00 2026 -0600'), and ISO strings.
    Returns None for empty/unparseable values.
    """
    if val is None or val == "":
        return None
    # Epoch milliseconds (BIGINT)
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(val / 1000, tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            return None
    val = str(val)
    # Try epoch string
    if val.isdigit():
        try:
            return datetime.fromtimestamp(int(val) / 1000, tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            return None
    # ISO 8601 (incl. trailing Z)
    iso_val = val.replace("Z", "+00:00", 1) if val.endswith("Z") else val
    try:
        dt = datetime.fromisoformat(iso_val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        pass
    # Git date format: 'Fri Feb 20 09:59:00 2026 -0600'
    try:
        return parsedate_to_datetime(val).isoformat()
    except (ValueError, TypeError):
        pass
    return None


def _sync_scored_commits(con: duckdb.DuckDBPyConnection, tracking_con, verbose: bool = False):
    """Import scored_commits from tracking DB with camelCase to snake_case mapping."""
    try:
        rows = tracking_con.execute(
            "SELECT commitHash, branchName, scoredAt, linesAdded, linesDeleted, "
            "tabLinesAdded, tabLinesDeleted, composerLinesAdded, composerLinesDeleted, "
            "humanLinesAdded, humanLinesDeleted, blankLinesAdded, blankLinesDeleted, "
            "commitMessage, commitDate, v1AiPercentage, v2AiPercentage "
            "FROM scored_commits"
        ).fetchall()
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        if verbose:
            print("  scored_commits table not found or tracking DB corrupt")
        return

    if not rows:
        return

    for row in rows:
        scored_at = _parse_tracking_timestamp(row[2])
        commit_date = _parse_tracking_timestamp(row[14])

        con.execute("""
            INSERT INTO scored_commits VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (commit_hash, branch_name) DO UPDATE SET
                scored_at = excluded.scored_at,
                lines_added = excluded.lines_added,
                lines_deleted = excluded.lines_deleted,
                tab_lines_added = excluded.tab_lines_added,
                tab_lines_deleted = excluded.tab_lines_deleted,
                composer_lines_added = excluded.composer_lines_added,
                composer_lines_deleted = excluded.composer_lines_deleted,
                human_lines_added = excluded.human_lines_added,
                human_lines_deleted = excluded.human_lines_deleted,
                blank_lines_added = excluded.blank_lines_added,
                blank_lines_deleted = excluded.blank_lines_deleted,
                commit_message = excluded.commit_message,
                commit_date = excluded.commit_date,
                v1_ai_percentage = excluded.v1_ai_percentage,
                v2_ai_percentage = excluded.v2_ai_percentage
        """, [
            row[0], row[1], "cursor", scored_at,
            row[3], row[4], row[5], row[6], row[7], row[8],
            row[9], row[10], row[11], row[12], row[13],
            commit_date, row[15], row[16],
        ])

    if verbose:
        print(f"  Scored commits: {len(rows)} imported")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="cursor-warehouse sync")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--full", action="store_true", help="Reset watermarks and re-sync everything")
    parser.add_argument("--compact", action="store_true", help="Vacuum and checkpoint DB after sync")
    parser.add_argument("--db", default=str(DB_PATH), help="Database path")
    args = parser.parse_args()

    t0 = time.time()
    try:
        con = duckdb.connect(args.db)
    except duckdb.IOException:
        # Another sync instance holds the database lock — exit silently
        return
    init_db(con)

    if args.full:
        con.execute("DELETE FROM _sync_state")
        if args.verbose:
            print("Reset all watermarks for full re-sync")

    if args.verbose:
        print(f"Syncing to {args.db}")

    projects_dir = CURSOR_DIR / "projects"
    sync_all(con, projects_dir, verbose=args.verbose)

    try:
        sync_tracking_db(con, verbose=args.verbose)
    except Exception as e:
        if args.verbose:
            print(f"  Tracking DB sync failed (non-fatal): {type(e).__name__}: {e}")

    if args.compact:
        if args.verbose:
            print("  Compacting database...")
        con.execute("CHECKPOINT")
        con.execute("VACUUM")

    con.close()

    elapsed = time.time() - t0
    if args.verbose:
        print(f"Done in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
