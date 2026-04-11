#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.2"]
# ///
"""cursor-warehouse: CLI query interface for Cursor agent session DuckDB store."""

import argparse
import sys
import time
from pathlib import Path

import duckdb

DB_PATH = Path.home() / ".cursor" / "cursor-warehouse.duckdb"


def connect(db_path: str) -> duckdb.DuckDBPyConnection:
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        print("Run sync.py first to create it.", file=sys.stderr)
        sys.exit(1)
    for attempt in range(5):
        try:
            return duckdb.connect(db_path, read_only=True)
        except duckdb.IOException:
            if attempt < 4:
                print("DB locked by sync, retrying...", file=sys.stderr)
                time.sleep(2)
            else:
                print("DB locked by sync. Try again in a moment.", file=sys.stderr)
                sys.exit(1)


def print_table(rows: list, headers: list[str], max_col: int = 60):
    if not rows:
        print("(no results)")
        return
    widths = [len(h) for h in headers]
    str_rows = []
    for row in rows:
        sr = []
        for i, val in enumerate(row):
            s = str(val) if val is not None else ""
            if len(s) > max_col:
                s = s[:max_col - 1] + "…"
            sr.append(s)
            if i < len(widths):
                widths[i] = max(widths[i], len(s))
        str_rows.append(sr)

    hdr = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(hdr)
    print("  ".join("─" * widths[i] for i in range(len(headers))))
    for sr in str_rows:
        print("  ".join(sr[i].ljust(widths[i]) if i < len(widths) else sr[i] for i in range(len(sr))))


def cmd_sessions(con: duckdb.DuckDBPyConnection, args):
    limit = args.limit or 20
    rows = con.execute(f"""
        SELECT
            strftime(s.created_at, '%m-%d %H:%M') as started,
            s.project_name,
            s.message_count as msgs,
            model_info.model as model,
            COALESCE(LEFT(s.first_prompt, 80), '') as prompt,
            s.session_id
        FROM sessions s
        LEFT JOIN (
            SELECT session_id, STRING_AGG(DISTINCT model, ', ' ORDER BY model) as model
            FROM messages WHERE model IS NOT NULL
            GROUP BY session_id
        ) model_info ON s.session_id = model_info.session_id
        ORDER BY s.created_at DESC
        LIMIT {limit}
    """).fetchall()
    print(f"Recent sessions (last {limit})\n")
    formatted = [
        (r[0] or "", r[1] or "", str(r[2] or 0), r[3] or "N/A", r[4] or "", r[5][:8])
        for r in rows
    ]
    print_table(formatted, ["Started", "Project", "Msgs", "Model", "Prompt", "ID"])


def cmd_tools(con: duckdb.DuckDBPyConnection, args):
    days = args.days or 7
    rows = con.execute(f"""
        SELECT
            tc.tool_name,
            COUNT(*) as calls,
            COUNT(DISTINCT tc.session_id) as sessions
        FROM tool_calls tc
        JOIN sessions s ON tc.session_id = s.session_id
        WHERE s.created_at >= current_date - INTERVAL '{days} days'
        GROUP BY tc.tool_name
        ORDER BY calls DESC
        LIMIT 30
    """).fetchall()
    print(f"Most used tools (last {days} days)\n")
    print_table(rows, ["Tool", "Calls", "Sessions"])


def cmd_search(con: duckdb.DuckDBPyConnection, args):
    q = args.query
    if not q:
        print("Usage: cursor-warehouse search <query>", file=sys.stderr)
        sys.exit(1)

    rows = con.execute("""
        SELECT
            m.session_id,
            s.project_name,
            m.type,
            strftime(s.created_at, '%m-%d %H:%M') as ts,
            LEFT(m.text_content, 200) as content
        FROM messages m
        LEFT JOIN sessions s ON m.session_id = s.session_id
        WHERE m.text_content ILIKE '%' || ? || '%'
        ORDER BY s.created_at DESC
        LIMIT 20
    """, [q]).fetchall()

    print(f"Messages matching '{q}'\n")
    formatted = [(r[3] or "", r[1] or "", r[2] or "", r[0][:8], r[4] or "") for r in rows]
    print_table(formatted, ["Time", "Project", "Type", "Session", "Content"])


def cmd_projects(con: duckdb.DuckDBPyConnection, args):
    rows = con.execute("""
        SELECT
            project_name,
            COUNT(*) as sessions,
            MIN(created_at)::DATE as first_seen,
            MAX(created_at)::DATE as last_seen,
            SUM(message_count) as total_msgs
        FROM sessions
        GROUP BY project_name
        ORDER BY last_seen DESC
        LIMIT 30
    """).fetchall()
    print("Project summary\n")
    formatted = [
        (r[0] or "", str(r[1]), str(r[2] or ""), str(r[3] or ""), str(r[4] or 0))
        for r in rows
    ]
    print_table(formatted, ["Project", "Sessions", "First", "Last", "Msgs"])


def cmd_size(con: duckdb.DuckDBPyConnection, args):
    db_file = Path(args.db)
    db_size = db_file.stat().st_size if db_file.exists() else 0

    counts = {}
    for table in ["sessions", "messages", "tool_calls", "scored_commits", "embeddings"]:
        try:
            r = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = r[0]
        except Exception:
            counts[table] = 0

    print("Database statistics\n")
    print(f"  DB file: {db_size / 1024 / 1024:.1f} MB")
    print()
    for table, count in counts.items():
        print(f"  {table:.<25} {count:>10,} rows")

    print("\nSync watermarks:\n")
    rows = con.execute("""
        SELECT source_name, last_run, files_synced, rows_synced
        FROM _sync_state ORDER BY source_name
    """).fetchall()
    print_table(rows, ["Source", "Last Run", "Files", "Rows"])


def cmd_tokens(con: duckdb.DuckDBPyConnection, args):
    """Show model distribution since Cursor has no per-message token counts."""
    days = args.days or 7
    rows = con.execute(f"""
        SELECT
            COALESCE(m.model, 'unknown') as model,
            COUNT(*) as messages,
            COUNT(DISTINCT m.session_id) as sessions
        FROM messages m
        JOIN sessions s ON m.session_id = s.session_id
        WHERE s.created_at >= current_date - INTERVAL '{days} days'
        GROUP BY m.model
        ORDER BY messages DESC
        LIMIT 20
    """).fetchall()
    print(f"Model usage (last {days} days) — Cursor does not expose per-message token counts\n")
    print_table(rows, ["Model", "Messages", "Sessions"])


def cmd_vsearch(con: duckdb.DuckDBPyConnection, args):
    """Delegate to vsearch.py (heavy deps loaded only when needed)."""
    import subprocess
    q = args.query
    if not q:
        print("Usage: cursor-warehouse vsearch <query>", file=sys.stderr)
        sys.exit(1)
    con.close()
    candidates = [
        Path(__file__).parent / "vsearch.py",
        *sorted(Path.home().glob(".cursor/plugins/cache/*/cursor-warehouse/*/scripts/vsearch.py"), reverse=True),
    ]
    vsearch = next((p for p in candidates if p.exists()), None)
    if not vsearch:
        print("vsearch.py not found. Install cursor-warehouse plugin.", file=sys.stderr)
        sys.exit(1)
    cmd = ["uv", "run", "--script", str(vsearch), q, "--db", args.db]
    if getattr(args, "project", None):
        cmd += ["--project", args.project]
    if getattr(args, "days", None):
        cmd += ["--days", str(args.days)]
    if getattr(args, "vtype", None):
        cmd += ["--type", args.vtype]
    if getattr(args, "limit", None):
        cmd += ["--limit", str(args.limit)]
    sys.exit(subprocess.call(cmd))


def cmd_sql(con: duckdb.DuckDBPyConnection, args):
    query = args.query
    if not query:
        print("Usage: cursor-warehouse sql \"SELECT ...\"", file=sys.stderr)
        sys.exit(1)
    try:
        result = con.execute(query)
        cols = [desc[0] for desc in result.description]
        rows = result.fetchall()
        print_table(rows, cols)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="cursor-warehouse query", prog="cursor-warehouse")
    parser.add_argument("--db", default=str(DB_PATH))

    sub = parser.add_subparsers(dest="command")

    p_sessions = sub.add_parser("sessions", help="Recent sessions")
    p_sessions.add_argument("--limit", "-n", type=int, default=20)

    p_tools = sub.add_parser("tools", help="Most used tools")
    p_tools.add_argument("--days", "-d", type=int, default=7)

    p_search = sub.add_parser("search", help="Full-text search")
    p_search.add_argument("query", nargs="?")

    p_projects = sub.add_parser("projects", help="Project summary")

    p_size = sub.add_parser("size", help="DB size and row counts")

    p_tokens = sub.add_parser("tokens", help="Model usage (no token counts available from Cursor)")
    p_tokens.add_argument("--days", "-d", type=int, default=7)

    p_vsearch = sub.add_parser("vsearch", help="Semantic vector search")
    p_vsearch.add_argument("query", nargs="?")
    p_vsearch.add_argument("--project", "-p")
    p_vsearch.add_argument("--days", "-d", type=int)
    p_vsearch.add_argument("--type", "-t", dest="vtype", choices=["message", "session"])
    p_vsearch.add_argument("--limit", "-n", type=int)

    p_sql = sub.add_parser("sql", help="Run raw SQL")
    p_sql.add_argument("query", nargs="?")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    con = connect(args.db)

    cmds = {
        "sessions": cmd_sessions,
        "tools": cmd_tools,
        "search": cmd_search,
        "projects": cmd_projects,
        "size": cmd_size,
        "tokens": cmd_tokens,
        "vsearch": cmd_vsearch,
        "sql": cmd_sql,
    }

    cmds[args.command](con, args)
    con.close()


if __name__ == "__main__":
    main()
