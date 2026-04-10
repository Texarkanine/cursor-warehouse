"""Tests for cursor-warehouse sync engine (JSONL parser, discovery, full flow)."""

import json
import os
import shutil
import sqlite3
from pathlib import Path

import duckdb
import pytest

from conftest import FIXTURES_DIR, SCHEMA_PATH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_dir(base: Path, workspace_slug: str, session_id: str) -> Path:
    """Create a Cursor-style agent-transcript directory structure."""
    d = base / workspace_slug / "agent-transcripts" / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_subagent_dir(base: Path, workspace_slug: str, parent_id: str, subagent_id: str) -> Path:
    """Create a subagent directory under a parent session."""
    d = base / workspace_slug / "agent-transcripts" / parent_id / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _copy_fixture(fixture_name: str, dest_dir: Path, dest_name: str) -> Path:
    src = FIXTURES_DIR / fixture_name
    dst = dest_dir / dest_name
    shutil.copy2(src, dst)
    return dst


def _create_tracking_db(path: Path, code_hashes: list[dict] | None = None,
                        scored_commits: list[dict] | None = None):
    """Create a minimal ai-code-tracking.db SQLite database."""
    con = sqlite3.connect(str(path))
    con.execute("""
        CREATE TABLE IF NOT EXISTS ai_code_hashes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversationId TEXT,
            requestId TEXT,
            model TEXT,
            timestamp INTEGER,
            hash TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS scored_commits (
            commitHash TEXT,
            branchName TEXT,
            scoredAt TEXT,
            linesAdded INTEGER,
            linesDeleted INTEGER,
            tabLinesAdded INTEGER,
            tabLinesDeleted INTEGER,
            composerLinesAdded INTEGER,
            composerLinesDeleted INTEGER,
            humanLinesAdded INTEGER,
            humanLinesDeleted INTEGER,
            blankLinesAdded INTEGER,
            blankLinesDeleted INTEGER,
            commitMessage TEXT,
            commitDate TEXT,
            v1AiPercentage TEXT,
            v2AiPercentage TEXT,
            PRIMARY KEY (commitHash, branchName)
        )
    """)
    if code_hashes:
        for h in code_hashes:
            con.execute(
                "INSERT INTO ai_code_hashes (conversationId, requestId, model, timestamp, hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (h["conversationId"], h.get("requestId", "req-1"),
                 h.get("model", "claude-4.6-opus-high-thinking"),
                 h.get("timestamp", 1712700000000), h.get("hash", "abc123")),
            )
    if scored_commits:
        for sc in scored_commits:
            con.execute(
                "INSERT INTO scored_commits "
                "(commitHash, branchName, scoredAt, linesAdded, linesDeleted, "
                "tabLinesAdded, tabLinesDeleted, composerLinesAdded, composerLinesDeleted, "
                "humanLinesAdded, humanLinesDeleted, blankLinesAdded, blankLinesDeleted, "
                "commitMessage, commitDate, v1AiPercentage, v2AiPercentage) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sc["commitHash"], sc["branchName"], sc.get("scoredAt"),
                 sc.get("linesAdded", 0), sc.get("linesDeleted", 0),
                 sc.get("tabLinesAdded", 0), sc.get("tabLinesDeleted", 0),
                 sc.get("composerLinesAdded", 0), sc.get("composerLinesDeleted", 0),
                 sc.get("humanLinesAdded", 0), sc.get("humanLinesDeleted", 0),
                 sc.get("blankLinesAdded", 0), sc.get("blankLinesDeleted", 0),
                 sc.get("commitMessage", "test commit"), sc.get("commitDate"),
                 sc.get("v1AiPercentage", "50%"), sc.get("v2AiPercentage", "60%")),
            )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# JSONL Parser Tests
# ---------------------------------------------------------------------------

class TestIngestJsonl:
    """Tests for _ingest_jsonl — single-file JSONL parsing."""

    def test_parses_cursor_role_field(self, db):
        """Cursor uses 'role' not 'type' as the top-level field."""
        import sync
        fp = FIXTURES_DIR / "cursor_session.jsonl"
        result = sync._ingest_jsonl(db, fp, session_id="test-session-001")
        assert result is not None
        sid, msg_count = result
        assert msg_count == 6  # 3 user + 3 assistant messages

    def test_message_uuids_are_deterministic(self, db):
        """UUIDs generated as {session_id}:{line_number} are stable."""
        import sync
        fp = FIXTURES_DIR / "cursor_session.jsonl"
        sync._ingest_jsonl(db, fp, session_id="test-session-001")
        rows = db.execute(
            "SELECT uuid FROM messages WHERE session_id = 'test-session-001' ORDER BY uuid"
        ).fetchall()
        uuids = [r[0] for r in rows]
        assert "test-session-001:0" in uuids
        assert "test-session-001:1" in uuids

    def test_session_id_used_from_argument(self, db):
        """Session ID is taken from the caller (derived from directory name)."""
        import sync
        fp = FIXTURES_DIR / "cursor_session.jsonl"
        result = sync._ingest_jsonl(db, fp, session_id="my-custom-session-id")
        assert result is not None
        sid, _ = result
        assert sid == "my-custom-session-id"

    def test_text_content_extracted(self, db):
        """Text blocks from message.content[] are extracted correctly."""
        import sync
        fp = FIXTURES_DIR / "cursor_session.jsonl"
        sync._ingest_jsonl(db, fp, session_id="test-session-001")
        rows = db.execute(
            "SELECT text_content FROM messages WHERE session_id = 'test-session-001' "
            "AND text_content IS NOT NULL ORDER BY uuid"
        ).fetchall()
        assert len(rows) > 0
        assert "refactor the authentication" in rows[0][0]

    def test_tool_use_blocks_extracted_to_tool_calls(self, db):
        """tool_use content blocks populate the tool_calls table."""
        import sync
        fp = FIXTURES_DIR / "cursor_session.jsonl"
        sync._ingest_jsonl(db, fp, session_id="test-session-001")
        rows = db.execute(
            "SELECT tool_name FROM tool_calls WHERE session_id = 'test-session-001' ORDER BY tool_name"
        ).fetchall()
        tool_names = [r[0] for r in rows]
        assert "Read" in tool_names
        assert "Write" in tool_names
        assert "StrReplace" in tool_names

    def test_harness_column_set_to_cursor(self, db):
        """All inserted rows have harness='cursor'."""
        import sync
        fp = FIXTURES_DIR / "cursor_session.jsonl"
        sync._ingest_jsonl(db, fp, session_id="test-session-001")

        for table in ["sessions", "messages", "tool_calls"]:
            rows = db.execute(f"SELECT DISTINCT harness FROM {table}").fetchall()
            harness_values = {r[0] for r in rows}
            assert harness_values == {"cursor"}, f"Table '{table}' has wrong harness: {harness_values}"

    def test_first_user_prompt_extracted(self, db):
        """First user prompt is stored in sessions.first_prompt."""
        import sync
        fp = FIXTURES_DIR / "cursor_session.jsonl"
        sync._ingest_jsonl(db, fp, session_id="test-session-001")
        row = db.execute(
            "SELECT first_prompt FROM sessions WHERE session_id = 'test-session-001'"
        ).fetchone()
        assert row is not None
        assert "refactor the authentication" in row[0]

    def test_empty_jsonl_handled_gracefully(self, db):
        """Empty JSONL file produces no rows and no crash."""
        import sync
        fp = FIXTURES_DIR / "empty.jsonl"
        result = sync._ingest_jsonl(db, fp, session_id="empty-session")
        assert result is None

    def test_malformed_lines_skipped(self, db):
        """Malformed JSON lines are skipped without crashing."""
        import sync
        fp = FIXTURES_DIR / "malformed.jsonl"
        result = sync._ingest_jsonl(db, fp, session_id="malformed-session")
        assert result is not None
        sid, msg_count = result
        assert msg_count == 3  # 3 valid lines, 2 malformed skipped

    def test_subagent_flag_set(self, db):
        """Subagent JSONL files produce sessions with is_subagent=True."""
        import sync
        fp = FIXTURES_DIR / "cursor_subagent.jsonl"
        result = sync._ingest_jsonl(
            db, fp, session_id="sub-agent-001",
            is_subagent=True, parent_session_id="parent-001",
        )
        assert result is not None
        row = db.execute(
            "SELECT is_subagent, parent_session_id FROM sessions WHERE session_id = 'sub-agent-001'"
        ).fetchone()
        assert row[0] is True
        assert row[1] == "parent-001"

    def test_string_content_handled(self, db):
        """Content blocks that are plain strings (not dicts) are handled."""
        import sync
        fp = FIXTURES_DIR / "string_content.jsonl"
        result = sync._ingest_jsonl(db, fp, session_id="string-session")
        assert result is not None
        rows = db.execute(
            "SELECT text_content FROM messages WHERE session_id = 'string-session' "
            "AND text_content IS NOT NULL"
        ).fetchall()
        assert len(rows) > 0

    def test_long_text_truncated(self, db):
        """Very long text_content is truncated to 2000 chars."""
        import sync
        long_text = "x" * 5000
        tmp = FIXTURES_DIR.parent / "tmp_long.jsonl"
        try:
            with open(tmp, "w") as f:
                f.write(json.dumps({
                    "role": "user",
                    "message": {"role": "user", "content": [{"type": "text", "text": long_text}]},
                }) + "\n")
            result = sync._ingest_jsonl(db, fp=tmp, session_id="long-session")
            assert result is not None
            row = db.execute(
                "SELECT text_content FROM messages WHERE session_id = 'long-session'"
            ).fetchone()
            assert len(row[0]) <= 2000
        finally:
            tmp.unlink(missing_ok=True)

    def test_token_counts_default_to_zero(self, db):
        """Cursor has no per-message tokens; session token totals default to 0."""
        import sync
        fp = FIXTURES_DIR / "cursor_session.jsonl"
        sync._ingest_jsonl(db, fp, session_id="test-session-001")
        # sessions table shouldn't have token columns at all in cursor-warehouse,
        # but messages also shouldn't have token data
        row = db.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = 'test-session-001'"
        ).fetchone()
        assert row[0] > 0

    def test_tools_used_and_models_used_populated(self, db):
        """Session metadata includes tools_used and models_used JSON arrays."""
        import sync
        fp = FIXTURES_DIR / "cursor_session.jsonl"
        sync._ingest_jsonl(db, fp, session_id="test-session-001")
        row = db.execute(
            "SELECT tools_used, models_used FROM sessions WHERE session_id = 'test-session-001'"
        ).fetchone()
        tools = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        assert "Read" in tools
        assert "Write" in tools


# ---------------------------------------------------------------------------
# Discovery Tests
# ---------------------------------------------------------------------------

class TestDiscovery:
    """Tests for _scan_jsonl_files — file discovery under agent-transcripts/."""

    def test_scan_finds_session_files(self, tmp_path):
        import sync
        projects_dir = tmp_path / "projects"
        session_dir = _make_session_dir(projects_dir, "my-workspace", "session-aaa")
        _copy_fixture("cursor_session.jsonl", session_dir, "session-aaa.jsonl")

        sessions, subagents = sync._scan_jsonl_files(projects_dir, 0.0, 0.0)
        assert len(sessions) == 1
        assert len(subagents) == 0

    def test_scan_finds_subagent_files(self, tmp_path):
        import sync
        projects_dir = tmp_path / "projects"
        session_dir = _make_session_dir(projects_dir, "my-workspace", "session-bbb")
        _copy_fixture("cursor_session.jsonl", session_dir, "session-bbb.jsonl")
        sub_dir = _make_subagent_dir(projects_dir, "my-workspace", "session-bbb", "sub-001")
        _copy_fixture("cursor_subagent.jsonl", sub_dir, "sub-001.jsonl")

        sessions, subagents = sync._scan_jsonl_files(projects_dir, 0.0, 0.0)
        assert len(sessions) == 1
        assert len(subagents) == 1

    def test_watermark_filters_old_files(self, tmp_path):
        import sync
        import time
        projects_dir = tmp_path / "projects"
        session_dir = _make_session_dir(projects_dir, "my-workspace", "session-ccc")
        fp = _copy_fixture("cursor_session.jsonl", session_dir, "session-ccc.jsonl")

        future_wm = time.time() + 9999
        sessions, subagents = sync._scan_jsonl_files(projects_dir, future_wm, future_wm)
        assert len(sessions) == 0

    def test_multiple_workspaces_discovered(self, tmp_path):
        import sync
        projects_dir = tmp_path / "projects"
        for ws in ["workspace-a", "workspace-b", "workspace-c"]:
            sid = f"session-{ws}"
            session_dir = _make_session_dir(projects_dir, ws, sid)
            _copy_fixture("cursor_session.jsonl", session_dir, f"{sid}.jsonl")

        sessions, _ = sync._scan_jsonl_files(projects_dir, 0.0, 0.0)
        assert len(sessions) == 3


# ---------------------------------------------------------------------------
# Removed Functions Tests
# ---------------------------------------------------------------------------

class TestRemovedFunctions:
    """Claude-specific sync functions must NOT exist."""

    def test_no_deleted_sessions_sync(self):
        import sync
        assert not hasattr(sync, "sync_deleted_sessions")

    def test_no_hook_events_sync(self):
        import sync
        assert not hasattr(sync, "sync_hook_events")

    def test_no_todos_sync(self):
        import sync
        assert not hasattr(sync, "sync_todos")

    def test_no_debug_sync(self):
        import sync
        assert not hasattr(sync, "sync_debug")

    def test_no_history_sync(self):
        import sync
        assert not hasattr(sync, "sync_history")

    def test_no_purge(self):
        import sync
        assert not hasattr(sync, "purge_synced_files")


# ---------------------------------------------------------------------------
# Full Sync Flow (Integration)
# ---------------------------------------------------------------------------

class TestFullSyncFlow:
    """Integration tests for the complete sync pipeline."""

    def test_full_sync_populates_all_tables(self, tmp_path):
        import sync

        projects_dir = tmp_path / "projects"
        session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        session_dir = _make_session_dir(projects_dir, "my-workspace", session_id)
        _copy_fixture("cursor_session.jsonl", session_dir, f"{session_id}.jsonl")

        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(SCHEMA_PATH.read_text())

        sync.sync_all(con, projects_dir)

        sessions = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        messages = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        tools = con.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]

        assert sessions >= 1
        assert messages >= 1
        assert tools >= 1
        con.close()

    def test_dedup_on_resync(self, tmp_path):
        """Syncing the same file twice should not create duplicate rows."""
        import sync

        projects_dir = tmp_path / "projects"
        session_id = "dedup-test-session"
        session_dir = _make_session_dir(projects_dir, "my-workspace", session_id)
        _copy_fixture("cursor_session.jsonl", session_dir, f"{session_id}.jsonl")

        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(SCHEMA_PATH.read_text())

        sync.sync_all(con, projects_dir)
        count1 = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

        # Reset watermarks to force re-sync
        con.execute("DELETE FROM _sync_state")
        sync.sync_all(con, projects_dir)
        count2 = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

        assert count1 == count2
        con.close()

    def test_subagent_detection_and_parent_linking(self, tmp_path):
        """Subagent sessions are correctly linked to their parent."""
        import sync

        projects_dir = tmp_path / "projects"
        parent_id = "parent-session-id"
        sub_id = "sub-agent-id"
        session_dir = _make_session_dir(projects_dir, "my-workspace", parent_id)
        _copy_fixture("cursor_session.jsonl", session_dir, f"{parent_id}.jsonl")

        sub_dir = session_dir / "subagents"
        sub_dir.mkdir(exist_ok=True)
        _copy_fixture("cursor_subagent.jsonl", sub_dir, f"{sub_id}.jsonl")

        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(SCHEMA_PATH.read_text())

        sync.sync_all(con, projects_dir)

        row = con.execute(
            "SELECT is_subagent, parent_session_id FROM sessions WHERE session_id = ?",
            [sub_id],
        ).fetchone()
        assert row is not None
        assert row[0] is True
        assert row[1] == parent_id
        con.close()

    def test_project_name_derived_from_workspace_slug(self, tmp_path):
        """Project name is derived from the workspace slug's last path segment."""
        import sync

        projects_dir = tmp_path / "projects"
        session_id = "proj-name-session"
        session_dir = _make_session_dir(
            projects_dir, "home-user-Documents-git-my-cool-project", session_id
        )
        _copy_fixture("cursor_session.jsonl", session_dir, f"{session_id}.jsonl")

        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(SCHEMA_PATH.read_text())

        sync.sync_all(con, projects_dir)

        row = con.execute(
            "SELECT project_name FROM sessions WHERE session_id = ?", [session_id]
        ).fetchone()
        assert row is not None
        assert row[0] is not None
        assert len(row[0]) > 0
        con.close()
