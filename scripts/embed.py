#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.2", "sentence-transformers>=3.0", "torch"]
# ///
"""cursor-warehouse: incremental vector embedding for semantic search."""

import argparse
import time
from collections import defaultdict
from pathlib import Path

import duckdb

from schema_util import ensure_sync_state_last_path

CURSOR_DIR = Path.home() / ".cursor"
DB_PATH = CURSOR_DIR / "cursor-warehouse.duckdb"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
MODEL_NAME = "all-MiniLM-L6-v2"
BATCH_SIZE = 256
MIN_TEXT_LEN = 30
# Short slash-commands (e.g. /cw-report) still deserve a vector; keep below MIN_TEXT_LEN.
MIN_USER_QUERY_LEN = 3
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def init_db(con: duckdb.DuckDBPyConnection):
    con.execute(SCHEMA_PATH.read_text())
    ensure_sync_state_last_path(con)
    con.execute("INSTALL vss; LOAD vss")
    con.execute("SET hnsw_enable_experimental_persistence = true")


def load_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_NAME)


def chunk_text(text: str) -> list[tuple[int, str]]:
    if len(text) <= CHUNK_SIZE:
        return [(0, text)]
    chunks = []
    start = 0
    idx = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append((idx, text[start:end]))
        start = end - CHUNK_OVERLAP
        idx += 1
    return chunks


def _vectors_to_nested_lists(embeddings) -> list[list[float]]:
    """Convert sentence-transformers encode output to nested Python lists.

    Row-by-row ``[e.tolist() for e in embeddings]`` is very slow on large
    matrices (e.g. 7k×384); one call to ``.tolist()`` on the full 2D array
    (NumPy ndarray or torch tensor) is much faster. Handles 1D (single row).
    Also accepts plain ``list[list[float]]`` (e.g. test doubles).
    """
    if isinstance(embeddings, list):
        if not embeddings:
            return []
        if isinstance(embeddings[0], (list, tuple)):
            return [list(row) for row in embeddings]
        return [list(embeddings)]
    if hasattr(embeddings, "detach"):
        embeddings = embeddings.detach().cpu()
    nested = embeddings.tolist()
    if not nested:
        return []
    # 2D: list of rows; 1D: single vector
    if isinstance(nested[0], (list, tuple)):
        return [list(row) for row in nested]
    return [nested]


def batch_encode(model, texts: list[str], verbose: bool = False) -> list[list[float]]:
    if not texts:
        return []
    embeddings = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        show_progress_bar=verbose,
    )
    if verbose:
        print("  Converting vectors to storage format…", flush=True)
    return _vectors_to_nested_lists(embeddings)


def _mean_pool_vectors(vectors: list[list[float]]) -> list[float]:
    """Element-wise mean of chunk embeddings (one vector per document)."""
    if not vectors:
        return []
    dim = len(vectors[0])
    n = len(vectors)
    return [sum(row[j] for row in vectors) / n for j in range(dim)]


def batch_encode_documents(model, texts: list[str], verbose: bool = False) -> list[list[float]]:
    """Encode one vector per document. Long texts are split with chunk_text then mean-pooled."""
    if not texts:
        return []
    doc_chunks: list[list[str]] = []
    for t in texts:
        doc_chunks.append([c for _, c in chunk_text(t)])

    flat: list[str] = []
    doc_idx_per_chunk: list[int] = []
    for di, parts in enumerate(doc_chunks):
        for c in parts:
            flat.append(c)
            doc_idx_per_chunk.append(di)

    flat_emb: list[list[float]] = []
    for i in range(0, len(flat), BATCH_SIZE):
        batch = flat[i : i + BATCH_SIZE]
        flat_emb.extend(batch_encode(model, batch, verbose=verbose))

    buckets: dict[int, list[list[float]]] = defaultdict(list)
    for vec, di in zip(flat_emb, doc_idx_per_chunk, strict=True):
        buckets[di].append(vec)

    return [_mean_pool_vectors(buckets[i]) for i in range(len(texts))]


# ---------------------------------------------------------------------------
# Embedding pipelines
# ---------------------------------------------------------------------------

def count_unembedded(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    msg_count = con.execute("""
        SELECT COUNT(*) FROM messages m
        LEFT JOIN embeddings e ON e.source_type = 'message'
            AND e.source_id = m.uuid
        WHERE e.source_id IS NULL
            AND m.text_content IS NOT NULL
            AND LENGTH(m.text_content) >= ?
    """, [MIN_TEXT_LEN]).fetchone()[0]

    # Stripped user_query (sync populates from extract_user_query — no XML framing)
    uq_count = con.execute("""
        SELECT COUNT(*) FROM messages m
        LEFT JOIN embeddings e ON e.source_type = 'message_user_query'
            AND e.source_id = m.uuid
        WHERE e.source_id IS NULL
            AND m.user_query IS NOT NULL
            AND LENGTH(TRIM(m.user_query)) >= ?
    """, [MIN_USER_QUERY_LEN]).fetchone()[0]

    sess_count = con.execute("""
        SELECT COUNT(*) FROM sessions s
        LEFT JOIN embeddings e ON e.source_type = 'session'
            AND e.source_id = s.session_id
        WHERE e.source_id IS NULL
            AND s.first_prompt IS NOT NULL
            AND LENGTH(s.first_prompt) >= ?
    """, [MIN_TEXT_LEN]).fetchone()[0]

    return {"message": msg_count, "message_user_query": uq_count, "session": sess_count}


def embed_messages(con: duckdb.DuckDBPyConnection, model, verbose: bool = False):
    if verbose:
        print("  Fetching messages (full text) from database…", flush=True)
    rows = con.execute("""
        SELECT m.session_id, m.uuid, m.text_content
        FROM messages m
        LEFT JOIN embeddings e ON e.source_type = 'message'
            AND e.source_id = m.uuid
        WHERE e.source_id IS NULL
            AND m.text_content IS NOT NULL
            AND LENGTH(m.text_content) >= ?
    """, [MIN_TEXT_LEN]).fetchall()

    if not rows:
        return 0

    if verbose:
        print(f"  Encoding {len(rows)} message texts (batch size {BATCH_SIZE})…", flush=True)
    texts = [r[2] for r in rows]
    embeddings = batch_encode_documents(model, texts, verbose=verbose)

    if verbose:
        print("  Building insert batch…", flush=True)
    batch = []
    for (_sid, uuid, text), emb in zip(rows, embeddings):
        preview = text[:200]
        batch.append(("message", uuid, 0, "cursor", preview, emb))

    if verbose:
        print(f"  Writing {len(batch)} rows to DuckDB…", flush=True)
    con.executemany(
        "INSERT INTO embeddings VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
        batch,
    )
    if verbose:
        print(f"  Messages (full text_content): {len(batch)} embedded")
    return len(batch)


def embed_message_user_queries(con: duckdb.DuckDBPyConnection, model, verbose: bool = False):
    """Embed stripped user intent (user_query) in addition to full message embeddings."""
    if verbose:
        print("  Fetching user_query rows from database…", flush=True)
    rows = con.execute("""
        SELECT m.session_id, m.uuid, m.user_query
        FROM messages m
        LEFT JOIN embeddings e ON e.source_type = 'message_user_query'
            AND e.source_id = m.uuid
        WHERE e.source_id IS NULL
            AND m.user_query IS NOT NULL
            AND LENGTH(TRIM(m.user_query)) >= ?
    """, [MIN_USER_QUERY_LEN]).fetchall()

    if not rows:
        return 0

    if verbose:
        print(f"  Encoding {len(rows)} user_query texts…", flush=True)
    texts = [r[2].strip() for r in rows]
    embeddings = batch_encode_documents(model, texts, verbose=verbose)

    batch = []
    for (_sid, uuid, _), text, emb in zip(rows, texts, embeddings):
        preview = text[:200]
        batch.append(("message_user_query", uuid, 0, "cursor", preview, emb))

    con.executemany(
        "INSERT INTO embeddings VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
        batch,
    )
    if verbose:
        print(f"  Messages (user_query only): {len(batch)} embedded")
    return len(batch)


def embed_sessions(con: duckdb.DuckDBPyConnection, model, verbose: bool = False):
    if verbose:
        print("  Fetching sessions (first_prompt) from database…", flush=True)
    rows = con.execute("""
        SELECT s.session_id, s.first_prompt
        FROM sessions s
        LEFT JOIN embeddings e ON e.source_type = 'session'
            AND e.source_id = s.session_id
        WHERE e.source_id IS NULL
            AND s.first_prompt IS NOT NULL
            AND LENGTH(s.first_prompt) >= ?
    """, [MIN_TEXT_LEN]).fetchall()

    if not rows:
        return 0

    if verbose:
        print(f"  Encoding {len(rows)} session prompts…", flush=True)
    texts = [r[1] for r in rows]
    embeddings = batch_encode_documents(model, texts, verbose=verbose)

    batch = []
    for (sid, text), emb in zip(rows, embeddings):
        batch.append(("session", sid, 0, "cursor", text[:200], emb))

    con.executemany(
        "INSERT INTO embeddings VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
        batch,
    )
    if verbose:
        print(f"  Sessions: {len(batch)} embedded")
    return len(batch)


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------

def clean_stale_embeddings(con: duckdb.DuckDBPyConnection, verbose: bool = False):
    """Remove embeddings for messages/sessions/user_query rows that no longer exist."""
    stale_msgs = con.execute("""
        SELECT e.source_id FROM embeddings e
        WHERE e.source_type = 'message'
        AND NOT EXISTS (
            SELECT 1 FROM messages m WHERE e.source_id = m.uuid
        )
    """).fetchall()

    stale_uq = con.execute("""
        SELECT e.source_id FROM embeddings e
        WHERE e.source_type = 'message_user_query'
        AND NOT EXISTS (
            SELECT 1 FROM messages m WHERE e.source_id = m.uuid
        )
    """).fetchall()

    stale_sess = con.execute("""
        SELECT e.source_id FROM embeddings e
        WHERE e.source_type = 'session'
        AND NOT EXISTS (
            SELECT 1 FROM sessions s WHERE e.source_id = s.session_id
        )
    """).fetchall()

    total = 0
    if stale_msgs:
        ids = [r[0] for r in stale_msgs]
        con.executemany(
            "DELETE FROM embeddings WHERE source_type = 'message' AND source_id = ?",
            [(i,) for i in ids],
        )
        total += len(ids)
    if stale_uq:
        ids = [r[0] for r in stale_uq]
        con.executemany(
            "DELETE FROM embeddings WHERE source_type = 'message_user_query' AND source_id = ?",
            [(i,) for i in ids],
        )
        total += len(ids)
    if stale_sess:
        ids = [r[0] for r in stale_sess]
        con.executemany(
            "DELETE FROM embeddings WHERE source_type = 'session' AND source_id = ?",
            [(i,) for i in ids],
        )
        total += len(ids)

    if verbose and total:
        print(f"  Cleaned {total} stale embeddings")
    return total


# ---------------------------------------------------------------------------
# HNSW index
# ---------------------------------------------------------------------------

def rebuild_hnsw(con: duckdb.DuckDBPyConnection, verbose: bool = False):
    t0 = time.time()
    con.execute("DROP INDEX IF EXISTS idx_embeddings_hnsw")
    row_count = con.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    if row_count > 0:
        con.execute("""
            CREATE INDEX idx_embeddings_hnsw
            ON embeddings USING HNSW (embedding)
            WITH (metric = 'cosine')
        """)
    if verbose:
        print(f"  HNSW index: {row_count} vectors ({time.time() - t0:.1f}s)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="cursor-warehouse embed")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--full", action="store_true", help="Re-embed everything")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    t0 = time.time()

    if not Path(args.db).exists():
        print("Database not found. Run sync.py first.")
        return

    con = duckdb.connect(args.db)
    init_db(con)

    if args.full:
        con.execute("DELETE FROM embeddings")
        if args.verbose:
            print("Cleared all embeddings for full re-embed")

    counts = count_unembedded(con)
    stale = clean_stale_embeddings(con, args.verbose)
    total_work = sum(counts.values()) + stale

    if args.verbose:
        print(
            f"Unembedded: {counts['message']} msgs (full), "
            f"{counts['message_user_query']} msgs (user_query), "
            f"{counts['session']} sessions"
        )

    if total_work == 0 and not args.full:
        if args.verbose:
            print("Nothing to embed.")
        con.close()
        return

    if args.verbose:
        print("Loading model...")
    model = load_model()

    if stale > 0:
        counts = count_unembedded(con)

    n_msg = embed_messages(con, model, args.verbose)
    n_uq = embed_message_user_queries(con, model, args.verbose)
    n_sess = embed_sessions(con, model, args.verbose)

    total = n_msg + n_uq + n_sess
    if total > 0 or stale > 0:
        rebuild_hnsw(con, args.verbose)

    con.close()

    if args.verbose:
        print(f"Done in {time.time() - t0:.1f}s — {total} new embeddings")


if __name__ == "__main__":
    main()
