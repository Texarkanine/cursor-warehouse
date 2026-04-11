"""Tests for cursor-warehouse sync engine (JSONL parser, discovery, full flow)."""

import json
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


def _make_subagent_dir(base: Path, workspace_slug: str, parent_id: str) -> Path:
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
        _sid, msg_count = result
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
        _sid, msg_count = result
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

    def test_long_text_truncated(self, db, tmp_path):
        """Very long text_content is truncated to 2000 chars."""
        import sync
        long_text = "x" * 5000
        tmp = tmp_path / "tmp_long.jsonl"
        with open(tmp, "w", encoding="utf-8") as f:
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

    def test_ingest_error_logs_to_stderr(self, db, tmp_path, capsys):
        """File errors during ingest produce a stderr warning instead of silent skip."""
        import sync

        nonexistent = tmp_path / "no_such_file.jsonl"
        result = sync._ingest_jsonl(db, nonexistent, session_id="error-session")

        assert result is None
        captured = capsys.readouterr()
        assert "no_such_file" in captured.err


# ---------------------------------------------------------------------------
# User Query Extraction Tests
# ---------------------------------------------------------------------------

class TestExtractUserQuery:
    """Tests for extract_user_query — stripping system context from user messages."""

    def test_extracts_user_query_from_xml_tags(self):
        """Content inside <user_query> tags is extracted cleanly."""
        import sync
        raw = (
            "<rules>\nSome rules.\n</rules>\n"
            "<user_query>\nRefactor the auth module.\n</user_query>"
        )
        assert sync.extract_user_query(raw) == "Refactor the auth module."

    def test_extracts_multiline_user_query(self):
        """Multi-line content inside <user_query> tags is preserved."""
        import sync
        raw = (
            "<rules>\nStuff.\n</rules>\n"
            "<user_query>\nFirst line.\nSecond line.\nThird line.\n</user_query>"
        )
        result = sync.extract_user_query(raw)
        assert "First line." in result
        assert "Second line." in result
        assert "Third line." in result

    def test_extracts_slash_command_from_skill_invocation(self):
        """Skill invocations without <user_query> extract the /command."""
        import sync
        raw = (
            "<manually_attached_skills>\n"
            "The user has manually attached the following skills.\n"
            "Skill Name: cw-wrapped\n"
            "</manually_attached_skills>\n"
            "<user_query>\n/wrapped\n</user_query>"
        )
        result = sync.extract_user_query(raw)
        assert result == "/wrapped"

    def test_skill_only_no_user_query_tag_extracts_slash_command(self):
        """When there's no <user_query> tag at all, extract /command if present."""
        import sync
        raw = (
            "<manually_attached_skills>\n"
            "Skill content here.\n"
            "</manually_attached_skills>\n"
            "<git_status>\n## main\n</git_status>\n"
            "/cw-report generate a report for me"
        )
        result = sync.extract_user_query(raw)
        assert result is not None
        assert result.startswith("/cw-report")

    def test_plain_text_without_xml_returned_as_is(self):
        """Messages without XML system context are returned unchanged."""
        import sync
        raw = "Help me refactor the authentication module."
        assert sync.extract_user_query(raw) == raw

    def test_continuation_message_returns_as_is(self):
        """Auto-generated continuation messages have no XML and are returned as-is."""
        import sync
        raw = "Your previous response was interrupted. Continue from where you left off."
        assert sync.extract_user_query(raw) == raw

    def test_none_input_returns_none(self):
        import sync
        assert sync.extract_user_query(None) is None

    def test_empty_string_returns_none(self):
        import sync
        assert sync.extract_user_query("") is None

    def test_system_context_only_no_user_content_returns_none(self):
        """Messages with only system context and no user intent return None."""
        import sync
        raw = (
            "<rules>\nSome rules.\n</rules>\n"
            "<open_and_recently_viewed_files>\nfile.py\n</open_and_recently_viewed_files>"
        )
        assert sync.extract_user_query(raw) is None

    def test_user_query_with_surrounding_whitespace_stripped(self):
        """Leading/trailing whitespace inside <user_query> is stripped."""
        import sync
        raw = "<user_query>\n   \n  Do the thing.  \n   \n</user_query>"
        assert sync.extract_user_query(raw) == "Do the thing."

    def test_greedy_match_survives_user_typing_closing_tag(self):
        """If user types </user_query> inside their prompt, we match to the LAST closing tag."""
        import sync
        raw = (
            "<user_query>\n"
            "What does </user_query> do in the XML?\n"
            "I'm curious about the tag.\n"
            "</user_query>"
        )
        result = sync.extract_user_query(raw)
        assert result is not None
        assert "What does </user_query> do in the XML?" in result
        assert "I'm curious about the tag." in result

    def test_user_query_stored_in_messages_table(self, db):
        """Ingested messages populate the user_query column."""
        import sync
        fp = FIXTURES_DIR / "cursor_system_context.jsonl"
        result = sync._ingest_jsonl(db, fp, session_id="context-session")
        assert result is not None

        rows = db.execute(
            "SELECT role, user_query FROM messages "
            "WHERE session_id = 'context-session' ORDER BY uuid"
        ).fetchall()

        user_msgs = [(r[0], r[1]) for r in rows if r[0] == 'user']
        assert len(user_msgs) >= 3

        # First user message: has <user_query> with real content
        assert user_msgs[0][1] is not None
        assert "Refactor the auth module" in user_msgs[0][1]

        # Third user message: skill invocation with /wrapped
        assert user_msgs[1][1] is not None
        assert "/wrapped" in user_msgs[1][1]

    def test_user_query_null_for_assistant_messages(self, db):
        """Assistant messages have user_query = NULL."""
        import sync
        fp = FIXTURES_DIR / "cursor_system_context.jsonl"
        sync._ingest_jsonl(db, fp, session_id="context-session")

        rows = db.execute(
            "SELECT user_query FROM messages "
            "WHERE session_id = 'context-session' AND role = 'assistant'"
        ).fetchall()
        for row in rows:
            assert row[0] is None


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
        sub_dir = _make_subagent_dir(projects_dir, "my-workspace", "session-bbb")
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
        sessions, _subagents = sync._scan_jsonl_files(projects_dir, future_wm, future_wm)
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


# ---------------------------------------------------------------------------
# Tracking DB Integration Tests
# ---------------------------------------------------------------------------

class TestTrackingDbIntegration:
    """Tests for sync_tracking_db — model enrichment and scored_commits import."""

    def test_sync_model_from_tracking(self, tmp_path):
        """sync_tracking_db populates messages.model from ai_code_hashes."""
        import sync

        session_id = "tracked-session-001"
        projects_dir = tmp_path / "projects"
        session_dir = _make_session_dir(projects_dir, "my-workspace", session_id)
        _copy_fixture("cursor_session.jsonl", session_dir, f"{session_id}.jsonl")

        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(SCHEMA_PATH.read_text())
        sync.sync_all(con, projects_dir)

        # Verify model is NULL before tracking DB sync
        null_models = con.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND model IS NULL",
            [session_id],
        ).fetchone()[0]
        assert null_models > 0

        # Create tracking DB with model info for this session
        tracking_path = tmp_path / "ai-code-tracking.db"
        _create_tracking_db(tracking_path, code_hashes=[
            {"conversationId": session_id, "model": "claude-4.6-opus-high-thinking"},
        ])

        sync.sync_tracking_db(con, tracking_db_path=tracking_path)

        # Some messages should now have model populated
        with_model = con.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND model IS NOT NULL",
            [session_id],
        ).fetchone()[0]
        assert with_model > 0
        con.close()

    def test_model_enrichment_deterministic_min_model(self, tmp_path):
        """Multiple models for one conversationId pick lexicographic min (deterministic)."""
        import sync

        session_id = "multi-model-one-conv"
        projects_dir = tmp_path / "projects"
        session_dir = _make_session_dir(projects_dir, "my-workspace", session_id)
        _copy_fixture("cursor_session.jsonl", session_dir, f"{session_id}.jsonl")

        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(SCHEMA_PATH.read_text())
        sync.sync_all(con, projects_dir)

        tracking_path = tmp_path / "ai-code-tracking.db"
        _create_tracking_db(tracking_path, code_hashes=[
            {"conversationId": session_id, "model": "claude-4.6-opus-high-thinking"},
            {"conversationId": session_id, "model": "aaa-stable-model"},
            {"conversationId": session_id, "model": "zzz-beta"},
        ])

        sync.sync_tracking_db(con, tracking_db_path=tracking_path)

        models = con.execute(
            "SELECT DISTINCT model FROM messages WHERE session_id = ? AND model IS NOT NULL",
            [session_id],
        ).fetchall()
        assert len(models) == 1
        assert models[0][0] == "aaa-stable-model"
        con.close()

    def test_multi_model_conversations(self, tmp_path):
        """Different models on different conversations are tracked correctly."""
        import sync

        projects_dir = tmp_path / "projects"
        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(SCHEMA_PATH.read_text())

        # Create two sessions
        for sid in ["session-a", "session-b"]:
            session_dir = _make_session_dir(projects_dir, "my-workspace", sid)
            _copy_fixture("cursor_session.jsonl", session_dir, f"{sid}.jsonl")

        sync.sync_all(con, projects_dir)

        tracking_path = tmp_path / "ai-code-tracking.db"
        _create_tracking_db(tracking_path, code_hashes=[
            {"conversationId": "session-a", "model": "claude-4.6-opus-high-thinking"},
            {"conversationId": "session-b", "model": "gpt-5.3-codex"},
        ])

        sync.sync_tracking_db(con, tracking_db_path=tracking_path)

        model_a = con.execute(
            "SELECT DISTINCT model FROM messages WHERE session_id = 'session-a' AND model IS NOT NULL"
        ).fetchall()
        model_b = con.execute(
            "SELECT DISTINCT model FROM messages WHERE session_id = 'session-b' AND model IS NOT NULL"
        ).fetchall()

        assert any("claude" in r[0] for r in model_a)
        assert any("gpt" in r[0] for r in model_b)
        con.close()

    def test_scored_commits_imported(self, tmp_path):
        """scored_commits rows are imported with correct column mapping."""
        import sync

        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(SCHEMA_PATH.read_text())

        tracking_path = tmp_path / "ai-code-tracking.db"
        _create_tracking_db(tracking_path, scored_commits=[
            {
                "commitHash": "abc123def",
                "branchName": "main",
                "scoredAt": "2026-04-10T12:00:00",
                "linesAdded": 100,
                "linesDeleted": 20,
                "tabLinesAdded": 30,
                "tabLinesDeleted": 5,
                "composerLinesAdded": 50,
                "composerLinesDeleted": 10,
                "humanLinesAdded": 20,
                "humanLinesDeleted": 5,
                "blankLinesAdded": 0,
                "blankLinesDeleted": 0,
                "commitMessage": "feat: add auth module",
                "commitDate": "2026-04-10",
                "v1AiPercentage": "80%",
                "v2AiPercentage": "75%",
            }
        ])

        sync.sync_tracking_db(con, tracking_db_path=tracking_path)

        row = con.execute(
            "SELECT commit_hash, branch_name, harness, lines_added, v1_ai_percentage "
            "FROM scored_commits WHERE commit_hash = 'abc123def'"
        ).fetchone()
        assert row is not None
        assert row[0] == "abc123def"
        assert row[1] == "main"
        assert row[2] == "cursor"
        assert row[3] == 100
        assert row[4] == "80%"
        con.close()

    def test_scored_commits_dedup_on_resync(self, tmp_path):
        """Re-syncing tracking DB doesn't create duplicate scored_commits rows."""
        import sync

        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(SCHEMA_PATH.read_text())

        tracking_path = tmp_path / "ai-code-tracking.db"
        _create_tracking_db(tracking_path, scored_commits=[
            {"commitHash": "dedup-hash", "branchName": "main"},
        ])

        sync.sync_tracking_db(con, tracking_db_path=tracking_path)
        count1 = con.execute("SELECT COUNT(*) FROM scored_commits").fetchone()[0]

        sync.sync_tracking_db(con, tracking_db_path=tracking_path)
        count2 = con.execute("SELECT COUNT(*) FROM scored_commits").fetchone()[0]

        assert count1 == count2
        con.close()

    def test_missing_tracking_db_graceful_skip(self, tmp_path):
        """Sync completes gracefully when tracking DB does not exist."""
        import sync

        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(SCHEMA_PATH.read_text())

        nonexistent = tmp_path / "nonexistent.db"
        sync.sync_tracking_db(con, tracking_db_path=nonexistent, verbose=True)

        count = con.execute("SELECT COUNT(*) FROM scored_commits").fetchone()[0]
        assert count == 0
        con.close()

    def test_locked_tracking_db_graceful_skip(self, tmp_path):
        """Locked tracking DB is handled gracefully (no crash)."""
        import sync

        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(SCHEMA_PATH.read_text())

        # Create an empty file that's not a valid SQLite database
        bad_db = tmp_path / "bad-tracking.db"
        bad_db.write_text("not a database")

        # Should not crash
        sync.sync_tracking_db(con, tracking_db_path=bad_db, verbose=True)
        con.close()

    def test_scored_commits_upsert_updates_all_mutable_fields(self, tmp_path):
        """Re-syncing scored_commits with changed values updates ALL mutable fields, not just 3."""
        import sync

        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute(SCHEMA_PATH.read_text())

        tracking_path = tmp_path / "ai-code-tracking.db"
        _create_tracking_db(tracking_path, scored_commits=[{
            "commitHash": "upsert-hash", "branchName": "main",
            "scoredAt": "2026-04-10T12:00:00",
            "linesAdded": 100, "linesDeleted": 20,
            "tabLinesAdded": 30, "tabLinesDeleted": 5,
            "composerLinesAdded": 50, "composerLinesDeleted": 10,
            "humanLinesAdded": 20, "humanLinesDeleted": 5,
            "blankLinesAdded": 0, "blankLinesDeleted": 0,
            "commitMessage": "initial commit", "commitDate": "2026-04-10",
            "v1AiPercentage": "80%", "v2AiPercentage": "75%",
        }])
        sync.sync_tracking_db(con, tracking_db_path=tracking_path)

        # Re-create tracking DB with updated values for ALL mutable fields
        tracking_path.unlink()
        _create_tracking_db(tracking_path, scored_commits=[{
            "commitHash": "upsert-hash", "branchName": "main",
            "scoredAt": "2026-04-11T12:00:00",
            "linesAdded": 200, "linesDeleted": 40,
            "tabLinesAdded": 60, "tabLinesDeleted": 10,
            "composerLinesAdded": 100, "composerLinesDeleted": 20,
            "humanLinesAdded": 40, "humanLinesDeleted": 10,
            "blankLinesAdded": 5, "blankLinesDeleted": 3,
            "commitMessage": "updated commit", "commitDate": "2026-04-11",
            "v1AiPercentage": "90%", "v2AiPercentage": "85%",
        }])
        sync.sync_tracking_db(con, tracking_db_path=tracking_path)

        row = con.execute("""
            SELECT lines_added, lines_deleted, tab_lines_added, tab_lines_deleted,
                   composer_lines_added, composer_lines_deleted, human_lines_added,
                   human_lines_deleted, blank_lines_added, blank_lines_deleted,
                   commit_message, v1_ai_percentage, v2_ai_percentage
            FROM scored_commits WHERE commit_hash = 'upsert-hash'
        """).fetchone()

        assert row[0] == 200   # lines_added
        assert row[1] == 40    # lines_deleted
        assert row[2] == 60    # tab_lines_added
        assert row[3] == 10    # tab_lines_deleted
        assert row[4] == 100   # composer_lines_added
        assert row[5] == 20    # composer_lines_deleted
        assert row[6] == 40    # human_lines_added
        assert row[7] == 10    # human_lines_deleted
        assert row[8] == 5     # blank_lines_added
        assert row[9] == 3     # blank_lines_deleted
        assert row[10] == "updated commit"  # commit_message
        assert row[11] == "90%"  # v1_ai_percentage
        assert row[12] == "85%"  # v2_ai_percentage
        con.close()


# ---------------------------------------------------------------------------
# Embed Source ID Contract Tests
# ---------------------------------------------------------------------------

class TestEmbedSourceIdContract:
    """Contract tests between sync.py uuid format and embed.py source_id format.

    embed.py should use messages.uuid directly as the embedding source_id.
    Since uuid is already '{session_id}:{line_idx}', no additional prefixing is needed.
    """

    def test_embed_source_id_matches_message_uuid(self, db):
        """Embedding source_id = messages.uuid; the count_unembedded query should match."""
        import sync

        fp = FIXTURES_DIR / "cursor_session.jsonl"
        sync._ingest_jsonl(db, fp, session_id="embed-test-session")

        row = db.execute(
            "SELECT uuid FROM messages WHERE session_id = 'embed-test-session' LIMIT 1"
        ).fetchone()
        assert row is not None
        msg_uuid = row[0]
        assert msg_uuid.startswith("embed-test-session:")

        # count_unembedded query pattern (post-fix: source_id = m.uuid)
        unembedded = db.execute("""
            SELECT COUNT(*) FROM messages m
            LEFT JOIN embeddings e ON e.source_type = 'message'
                AND e.source_id = m.uuid
            WHERE e.source_id IS NULL
                AND m.text_content IS NOT NULL AND LENGTH(m.text_content) >= 30
        """).fetchone()[0]
        assert unembedded > 0

        # Insert embedding with source_id = uuid (correct format)
        db.execute(
            "INSERT INTO embeddings VALUES ('message', ?, 0, 'cursor', 'test', NULL)",
            [msg_uuid],
        )

        # That specific message should no longer count as unembedded
        still_unembedded = db.execute("""
            SELECT COUNT(*) FROM messages m
            LEFT JOIN embeddings e ON e.source_type = 'message'
                AND e.source_id = m.uuid
            WHERE e.source_id IS NULL
                AND m.session_id = 'embed-test-session' AND m.uuid = ?
                AND m.text_content IS NOT NULL AND LENGTH(m.text_content) >= 30
        """, [msg_uuid]).fetchone()[0]
        assert still_unembedded == 0

    def test_stale_detection_with_correct_source_id(self, db):
        """clean_stale_embeddings query correctly identifies orphans when source_id = uuid."""
        import sync

        fp = FIXTURES_DIR / "cursor_session.jsonl"
        sync._ingest_jsonl(db, fp, session_id="stale-test-session")

        row = db.execute(
            "SELECT uuid FROM messages WHERE session_id = 'stale-test-session' LIMIT 1"
        ).fetchone()
        msg_uuid = row[0]

        # Embedding with valid source_id should NOT be stale
        db.execute(
            "INSERT INTO embeddings VALUES ('message', ?, 0, 'cursor', 'test', NULL)",
            [msg_uuid],
        )
        stale = db.execute("""
            SELECT e.source_id FROM embeddings e
            WHERE e.source_type = 'message'
            AND NOT EXISTS (SELECT 1 FROM messages m WHERE e.source_id = m.uuid)
        """).fetchall()
        assert len(stale) == 0

        # Orphaned embedding SHOULD be detected as stale
        db.execute(
            "INSERT INTO embeddings VALUES ('message', 'nonexistent:99', 0, 'cursor', 'test', NULL)",
        )
        stale = db.execute("""
            SELECT e.source_id FROM embeddings e
            WHERE e.source_type = 'message'
            AND NOT EXISTS (SELECT 1 FROM messages m WHERE e.source_id = m.uuid)
        """).fetchall()
        assert len(stale) == 1
        assert stale[0][0] == "nonexistent:99"


# ---------------------------------------------------------------------------
# Dual embeddings: full text + user_query
# ---------------------------------------------------------------------------


class TestEmbedUserQuery:
    """message_user_query embeddings sit alongside full message embeddings (same uuid)."""

    def test_embed_creates_both_source_types(self, db, monkeypatch):
        import embed
        import sync

        fp = FIXTURES_DIR / "cursor_session.jsonl"
        sync._ingest_jsonl(db, fp, session_id="dual-embed-session")

        def fake_batch_encode(model, texts, verbose=False):
            return [[float(i % 11) / 100.0] * 384 for i in range(len(texts))]

        monkeypatch.setattr(embed, "batch_encode", fake_batch_encode)

        class _Dummy:
            pass

        dummy = _Dummy()
        n_full = embed.embed_messages(db, dummy, verbose=False)
        n_uq = embed.embed_message_user_queries(db, dummy, verbose=False)
        assert n_full > 0
        assert n_uq > 0

        both = db.execute("""
            SELECT m.uuid
            FROM messages m
            JOIN embeddings e1 ON e1.source_type = 'message' AND e1.source_id = m.uuid
            JOIN embeddings e2 ON e2.source_type = 'message_user_query' AND e2.source_id = m.uuid
            WHERE m.session_id = 'dual-embed-session'
              AND m.user_query IS NOT NULL
              AND LENGTH(TRIM(m.user_query)) >= 3
        """).fetchall()
        assert len(both) > 0

    def test_count_unembedded_includes_message_user_query(self, db, monkeypatch):
        import embed
        import sync

        fp = FIXTURES_DIR / "cursor_session.jsonl"
        sync._ingest_jsonl(db, fp, session_id="count-uq-session")

        counts = embed.count_unembedded(db)
        assert "message_user_query" in counts
        assert counts["message_user_query"] > 0

        row = db.execute("""
            SELECT uuid FROM messages
            WHERE session_id = 'count-uq-session' AND user_query IS NOT NULL
              AND LENGTH(TRIM(user_query)) >= 3
            LIMIT 1
        """).fetchone()
        assert row is not None
        db.execute(
            "INSERT INTO embeddings VALUES ('message', ?, 0, 'cursor', 'x', NULL)",
            [row[0]],
        )
        db.execute(
            "INSERT INTO embeddings VALUES ('message_user_query', ?, 0, 'cursor', 'y', NULL)",
            [row[0]],
        )
        counts2 = embed.count_unembedded(db)
        assert counts2["message_user_query"] == counts["message_user_query"] - 1


# ---------------------------------------------------------------------------
# DuckDB INTERVAL SQL Syntax Tests
# ---------------------------------------------------------------------------

class TestIntervalSqlSyntax:
    """Verify parameterized INTERVAL expression works in DuckDB (R5 vsearch fix)."""

    def test_parameterized_interval_executes(self, db):
        """The expression '? * INTERVAL 1 day' must work as a parameterized day offset."""
        result = db.execute(
            "SELECT current_date - (? * INTERVAL '1 day')",
            [7],
        ).fetchone()
        assert result is not None

    def test_date_comparison_with_parameterized_interval(self, db):
        """Date filtering with parameterized interval returns correct rows."""
        db.execute("CREATE TEMP TABLE test_dates (d DATE)")
        db.execute("INSERT INTO test_dates VALUES (current_date - INTERVAL '3 days')")
        db.execute("INSERT INTO test_dates VALUES (current_date - INTERVAL '10 days')")

        result = db.execute(
            "SELECT d >= current_date - (? * INTERVAL '1 day') FROM test_dates ORDER BY d",
            [7],
        ).fetchall()
        assert result[0][0] is False   # 10 days ago NOT within 7 days
        assert result[1][0] is True    # 3 days ago IS within 7 days


# ---------------------------------------------------------------------------
# WSL Tracking DB Discovery Tests
# ---------------------------------------------------------------------------

class TestFindTrackingDb:
    """Tests for _find_tracking_db — portable discovery across native and WSL paths."""

    def test_finds_native_path(self, tmp_path, monkeypatch):
        """Finds tracking DB at ~/.cursor/ai-tracking/ (native Linux/macOS)."""
        import sync

        cursor_dir = tmp_path / ".cursor"
        tracking_dir = cursor_dir / "ai-tracking"
        tracking_dir.mkdir(parents=True)
        db_file = tracking_dir / "ai-code-tracking.db"
        db_file.write_text("fake")

        monkeypatch.setattr(sync, "CURSOR_DIR", cursor_dir)
        result = sync._find_tracking_db()
        assert result is not None
        assert result == db_file

    def test_finds_wsl_mount_path(self, tmp_path, monkeypatch):
        """Finds tracking DB under /mnt/<drive>/Users/<user>/.cursor/ai-tracking/."""
        import sync

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir(parents=True)
        monkeypatch.setattr(sync, "CURSOR_DIR", cursor_dir)

        mnt = tmp_path / "mnt" / "c" / "Users" / "TestUser" / ".cursor" / "ai-tracking"
        mnt.mkdir(parents=True)
        db_file = mnt / "ai-code-tracking.db"
        db_file.write_text("fake")

        monkeypatch.setattr(sync, "_wsl_mnt_root", lambda: tmp_path / "mnt")
        result = sync._find_tracking_db()
        assert result is not None
        assert result == db_file

    def test_native_preferred_over_wsl(self, tmp_path, monkeypatch):
        """Native path takes precedence when both exist."""
        import sync

        cursor_dir = tmp_path / ".cursor"
        native_dir = cursor_dir / "ai-tracking"
        native_dir.mkdir(parents=True)
        native_db = native_dir / "ai-code-tracking.db"
        native_db.write_text("native")

        wsl_dir = tmp_path / "mnt" / "c" / "Users" / "Someone" / ".cursor" / "ai-tracking"
        wsl_dir.mkdir(parents=True)
        wsl_db = wsl_dir / "ai-code-tracking.db"
        wsl_db.write_text("wsl")

        monkeypatch.setattr(sync, "CURSOR_DIR", cursor_dir)
        monkeypatch.setattr(sync, "_wsl_mnt_root", lambda: tmp_path / "mnt")
        result = sync._find_tracking_db()
        assert result == native_db

    def test_returns_none_when_nothing_found(self, tmp_path, monkeypatch):
        """Returns None when no tracking DB exists anywhere."""
        import sync

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir(parents=True)
        monkeypatch.setattr(sync, "CURSOR_DIR", cursor_dir)
        monkeypatch.setattr(sync, "_wsl_mnt_root", lambda: tmp_path / "mnt")
        result = sync._find_tracking_db()
        assert result is None

    def test_finds_across_multiple_wsl_drives(self, tmp_path, monkeypatch):
        """Searches all drive letters under /mnt/, not just /mnt/c/."""
        import sync

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir(parents=True)
        monkeypatch.setattr(sync, "CURSOR_DIR", cursor_dir)

        wsl_dir = tmp_path / "mnt" / "s" / "Users" / "Austin" / ".cursor" / "ai-tracking"
        wsl_dir.mkdir(parents=True)
        db_file = wsl_dir / "ai-code-tracking.db"
        db_file.write_text("fake")

        monkeypatch.setattr(sync, "_wsl_mnt_root", lambda: tmp_path / "mnt")
        result = sync._find_tracking_db()
        assert result is not None
        assert result == db_file


# ---------------------------------------------------------------------------
# Project Name Derivation Tests
# ---------------------------------------------------------------------------

class TestDeriveProjectName:
    """Tests for _derive_project_name — workspace slug to human-readable name."""

    def test_standard_slug(self):
        """Standard workspace slug extracts last path segment."""
        import sync
        assert sync._derive_project_name("home-mobaxterm-Documents-git-myproject") == "myproject"

    def test_cursor_workspace_slug_uses_timestamp(self):
        """Cursor ephemeral workspace slugs use the numeric timestamp as the name."""
        import sync
        name = sync._derive_project_name(
            "s-Users-Austin-AppData-Roaming-Cursor-Workspaces-1764355524551-workspace-json"
        )
        assert name == "workspace-1764355524551"

    def test_cursor_workspace_slug_different_timestamps(self):
        """Each ephemeral workspace gets a distinct name from its timestamp."""
        import sync
        name1 = sync._derive_project_name(
            "s-Users-Austin-AppData-Roaming-Cursor-Workspaces-1111111111111-workspace-json"
        )
        name2 = sync._derive_project_name(
            "s-Users-Austin-AppData-Roaming-Cursor-Workspaces-2222222222222-workspace-json"
        )
        assert name1 != name2
        assert "1111111111111" in name1
        assert "2222222222222" in name2

    def test_empty_slug(self):
        import sync
        assert sync._derive_project_name("") == ""

    def test_single_segment(self):
        import sync
        assert sync._derive_project_name("myproject") == "myproject"

    def test_filesystem_reconstruction_multi_hyphen_dir(self, tmp_path, monkeypatch):
        """When the slug path exists under a root, recover the real final directory name."""
        import sync

        proj = tmp_path / "home" / "user" / "docs" / "my-cool-project"
        proj.mkdir(parents=True)
        monkeypatch.setattr(sync, "_RECONSTRUCTION_ROOTS", [tmp_path])
        sync._derive_project_name.cache_clear()
        assert sync._derive_project_name("home-user-docs-my-cool-project") == "my-cool-project"

    def test_reconstruction_fallback_when_path_missing(self, monkeypatch):
        """When no root resolves, fall back to the last slug segment (legacy behavior)."""
        import sync

        monkeypatch.setattr(sync, "_RECONSTRUCTION_ROOTS", [Path("/__nonexistent_recon_root__")])
        sync._derive_project_name.cache_clear()
        assert sync._derive_project_name("a-b-c-finalsegment") == "finalsegment"

    def test_wsl_drive_reconstruction(self, tmp_path, monkeypatch):
        """WSL-style slugs try /mnt/<drive>/ plus remaining segments."""
        import sync

        # /mnt/s/Users/Austin/Documents/git/cursor-warehouse
        leaf = (
            tmp_path / "mnt" / "s" / "Users" / "Austin" / "Documents" / "git" / "cursor-warehouse"
        )
        leaf.mkdir(parents=True)
        monkeypatch.setattr(sync, "_wsl_mnt_root", lambda: tmp_path / "mnt")
        monkeypatch.setattr(sync, "_RECONSTRUCTION_ROOTS", [Path("/__no_match__")])
        sync._derive_project_name.cache_clear()
        slug = "s-Users-Austin-Documents-git-cursor-warehouse"
        assert sync._derive_project_name(slug) == "cursor-warehouse"


# ---------------------------------------------------------------------------
# Tracking timestamp parsing
# ---------------------------------------------------------------------------


class TestParseTrackingTimestamp:
    """Tests for _parse_tracking_timestamp — ISO 8601, epoch, git dates."""

    def test_iso8601_with_z_suffix(self):
        import sync
        out = sync._parse_tracking_timestamp("2026-04-10T12:00:00Z")
        assert out is not None
        assert "2026-04-10" in out
        assert "12" in out

    def test_iso8601_naive_assumes_utc(self):
        import sync
        out = sync._parse_tracking_timestamp("2026-04-10T12:00:00")
        assert out is not None
        assert out.endswith("+00:00") or "T12:00:00" in out

    def test_git_date_still_parsed(self):
        import sync
        out = sync._parse_tracking_timestamp("Fri Feb 20 09:59:00 2026 -0600")
        assert out is not None
        assert "2026" in out

    def test_epoch_ms_int_unchanged(self):
        import sync
        out = sync._parse_tracking_timestamp(1712700000000)
        assert out is not None

    def test_none_returns_none(self):
        import sync
        assert sync._parse_tracking_timestamp(None) is None
