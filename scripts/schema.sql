-- cursor-warehouse schema
-- DuckDB DDL for Cursor agent session data

-- Sync watermarks for incremental ingest
CREATE TABLE IF NOT EXISTS _sync_state (
    source_name VARCHAR PRIMARY KEY,
    last_mtime DOUBLE,
    last_run TIMESTAMP DEFAULT current_timestamp,
    files_synced INTEGER DEFAULT 0,
    rows_synced BIGINT DEFAULT 0
);

-- Session-level aggregates from agent-transcripts/*.jsonl
CREATE TABLE IF NOT EXISTS sessions (
    session_id VARCHAR PRIMARY KEY,
    harness TEXT NOT NULL DEFAULT 'cursor',
    project_path VARCHAR,
    project_name VARCHAR,
    created_at TIMESTAMP,
    modified_at TIMESTAMP,
    message_count INTEGER,
    tools_used JSON,
    models_used JSON,
    first_prompt VARCHAR,
    file_path VARCHAR,
    is_subagent BOOLEAN DEFAULT FALSE,
    parent_session_id VARCHAR
);

-- Individual turns from JSONL
CREATE TABLE IF NOT EXISTS messages (
    session_id VARCHAR,
    uuid VARCHAR,
    harness TEXT NOT NULL DEFAULT 'cursor',
    type VARCHAR,
    timestamp TIMESTAMP,
    role VARCHAR,
    model VARCHAR,
    content_types JSON,
    tool_name VARCHAR,
    text_content VARCHAR,
    user_query VARCHAR,
    PRIMARY KEY (session_id, uuid)
);

-- Extracted tool calls from assistant content blocks
CREATE TABLE IF NOT EXISTS tool_calls (
    session_id VARCHAR,
    message_uuid VARCHAR,
    idx INTEGER,
    harness TEXT NOT NULL DEFAULT 'cursor',
    tool_name VARCHAR,
    tool_input VARCHAR,
    timestamp TIMESTAMP,
    PRIMARY KEY (session_id, message_uuid, idx)
);

-- Vector embeddings for semantic search
CREATE TABLE IF NOT EXISTS embeddings (
    source_type VARCHAR NOT NULL,
    source_id VARCHAR NOT NULL,
    chunk_idx INTEGER DEFAULT 0,
    harness TEXT NOT NULL DEFAULT 'cursor',
    text_preview VARCHAR,
    embedding FLOAT[384],
    PRIMARY KEY (source_type, source_id, chunk_idx)
);

-- Commit-level AI attribution from ai-code-tracking.db
CREATE TABLE IF NOT EXISTS scored_commits (
    commit_hash VARCHAR,
    branch_name VARCHAR,
    harness TEXT NOT NULL DEFAULT 'cursor',
    scored_at TIMESTAMP,
    lines_added INTEGER,
    lines_deleted INTEGER,
    tab_lines_added INTEGER,
    tab_lines_deleted INTEGER,
    composer_lines_added INTEGER,
    composer_lines_deleted INTEGER,
    human_lines_added INTEGER,
    human_lines_deleted INTEGER,
    blank_lines_added INTEGER,
    blank_lines_deleted INTEGER,
    commit_message VARCHAR,
    commit_date TIMESTAMP,
    v1_ai_percentage VARCHAR,
    v2_ai_percentage VARCHAR,
    PRIMARY KEY (commit_hash, branch_name)
);
