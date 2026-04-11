"""Tests for embed.py chunking and per-document aggregation (Round 3 RW8)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import embed  # noqa: E402


class FakeModel:
    """Deterministic encode: row i uses v[0]=len(text) for easy mean-pool checks."""

    def encode(self, texts, batch_size=None, show_progress_bar=False):
        dim = 384
        out = []
        for t in texts:
            v = [0.0] * dim
            v[0] = float(len(t))
            out.append(v)
        return out


def test_mean_pool_vectors_averages_rows():
    assert embed._mean_pool_vectors([[1.0, 2.0], [3.0, 4.0]]) == [2.0, 3.0]


def test_short_text_single_chunk_matches_batch_encode():
    short = "y" * 100
    model = FakeModel()
    direct = embed.batch_encode(model, [short], verbose=False)[0]
    pooled = embed.batch_encode_documents(model, [short], verbose=False)[0]
    assert direct == pooled


def test_long_text_mean_pool_differs_from_first_chunk_only():
    """Multi-chunk documents must not collapse to the first chunk's embedding."""
    long_text = "x" * (embed.CHUNK_SIZE + 50)
    chunks = embed.chunk_text(long_text)
    assert len(chunks) >= 2
    model = FakeModel()
    first_only = embed.batch_encode(model, [chunks[0][1]], verbose=False)[0]
    pooled = embed.batch_encode_documents(model, [long_text], verbose=False)[0]
    assert len(pooled) == 384
    assert pooled[0] != first_only[0]
