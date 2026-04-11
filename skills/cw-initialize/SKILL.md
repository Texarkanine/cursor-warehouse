---
name: "cw:initialize"
description: First-time setup for cursor-warehouse. Walks through prerequisites, optional user-level uv config for PyTorch, one-time full sync, one-time embeddings, and optional dashboard. Ask before writing global uv config.
---

# Initialize — First-Time Setup (`cw:initialize`)

Run this skill **once** after installing the cursor-warehouse plugin (or cloning the repo). It is the supported path to a working warehouse: **prerequisites → optional GPU-friendly PyTorch for `uv` → full sync → first embed → optional dashboard**.

The README defers here for the full conversational flow; keep this skill and README aligned.

## How you should behave

1. **Conversational and ordered** — Check prerequisites **before** running heavy commands. Explain what each step does in plain language.
2. **Ask permission** before **creating or editing** user-level **`uv`** config for PyTorch (`~/.config/uv/uv.toml` on Linux/macOS, `%APPDATA%\uv\uv.toml` on Windows). Do **not** write that file without explicit user consent. Explain *why* it helps (see below).
3. **Confirm** after major steps (sync finished, embed finished) with a short summary (counts, time, errors).
4. If the user skips optional steps, note what they lose (e.g. no semantic search without embed).

## Why PyTorch needs a user-level `uv` index (when relevant)

`embed.py` declares `torch` via PEP 723 (`uv run --script`). Many **Linux + NVIDIA** setups need PyTorch **CUDA** wheels from **`https://download.pytorch.org/whl/cu126`** (or `cu118`, etc.), not only the default PyPI resolution. A **named extra index** in **`uv.toml`** lets `uv` resolve `torch` to a build that matches the GPU/driver. **Apple Silicon** and **CPU-only** users should **not** add a `cu*` URL — they use the **macOS** or **CPU** install line from [PyTorch’s matrix](https://pytorch.org/get-started/locally/) instead.

If the user opts out of global config, they can still run embed on **CPU** (`CUDA_VISIBLE_DEVICES=""`) or configure PyTorch manually later.

## Finding the scripts

`CURSOR_PLUGIN_ROOT` is set when the plugin runs the skill; in dev it may be unset. Resolve **`PLUGIN_SCRIPTS`** once per session (symlink-safe):

```bash
PLUGIN_SCRIPTS="${CURSOR_PLUGIN_ROOT:+$CURSOR_PLUGIN_ROOT/scripts}"
if [ -z "$PLUGIN_SCRIPTS" ] || [ ! -d "$PLUGIN_SCRIPTS" ]; then
  PLUGIN_SCRIPTS="$(dirname "$(find -L ~/.cursor/plugins -name sync.py -path '*/cursor-warehouse/*/sync.py' 2>/dev/null | head -1)")"
fi
```

## Phase A — Prerequisites (check, don’t assume)

Walk through with the user:

| Check | What to verify |
|-------|----------------|
| **uv** | `uv --version` works ([uv](https://docs.astral.sh/uv/) installed). |
| **Data** | `~/.cursor/projects/` exists and contains `**/agent-transcripts/**/*.jsonl` (or say sync will import whatever is present). |
| **Platform** | **Linux + NVIDIA**: offer the optional `uv.toml` PyTorch index. **macOS**: no `cu*` index — point to PyTorch macOS instructions. **CPU-only / unsure**: embed can still run on CPU (slower). |
| **Time** | Full sync is often seconds–minutes; first embed can be **long** on large histories (many minutes). |

If prerequisites fail, fix or set expectations before Phase B.

## Phase B — Optional: user-level `uv` config for PyTorch (permission required)

**Only for users who want GPU-accelerated embed on Linux + NVIDIA** (or who hit wrong-`torch` / CUDA errors). **Stop and ask:**

- Whether they want you to add an **extra package index** to their **user** `uv.toml` so `torch` resolves from PyTorch’s CUDA wheel repo.
- Show the **exact** path you will create or edit and a **minimal** example (they should pick `cu126`, `cu118`, etc. to match [get-started locally](https://pytorch.org/get-started/locally/)):

```toml
[[index]]
name = "pytorch-cu126"
url = "https://download.pytorch.org/whl/cu126"
```

**If they say yes** — write or merge that file (merge with existing `[[index]]` entries if present; don’t delete unrelated config). **If they say no** — continue to Phase C; mention CPU-only / manual install / README troubleshooting.

**Optional verification** after config (or to test torch without embed):

```bash
uv run --script "$PLUGIN_SCRIPTS/uv_torch_smoke.py"
```

If **`uv`** fails on NVIDIA wheels with **invalid wheel / metadata** errors, suggest **`uv cache clean`** and **`uv self update`**, then retry (see README).

## Phase C — One-time full sync (required)

Import all historical Cursor agent transcripts and tracking data into DuckDB:

```bash
uv run --script "$PLUGIN_SCRIPTS/sync.py" --full --verbose
```

This discovers JSONL under `~/.cursor/projects/*/agent-transcripts/`, fills `sessions` / `messages` / `tool_calls`, and enriches from `ai-code-tracking.db` when present. Report high-level counts when done.

## Phase D — One-time embed (strongly recommended)

Embeddings power **`cw:recall`** semantic search and `vsearch.py`. **Ask** before running — it downloads the sentence-transformer (~90MB first time) and can take **many minutes** on large warehouses; the script prints progress with `--verbose`.

```bash
uv run --script "$PLUGIN_SCRIPTS/embed.py" --verbose
```

If the user skipped Phase B and embed fails on CUDA, suggest CPU (`CUDA_VISIBLE_DEVICES=""`) or returning to Phase B.

## Phase E — Dashboard (optional)

Local analytics UI (also started by hooks later):

```bash
uv run --script "$PLUGIN_SCRIPTS/dashboard.py" &
```

Open `http://127.0.0.1:3141`.

## Phase F — Done

Confirm the warehouse is ready. Remind them of:

- **`cw:query`** — SQL over the warehouse  
- **`cw:recall`** — keyword + semantic search (needs embeddings)  
- **`cw:report`** / **`cw:wrapped`** — analytics  

Incremental sync runs on session start via the plugin hook; they don’t need `--full` again unless rebuilding.

## Reference

- **README** — [Embeddings and PyTorch](../../README.md) (section *Embeddings and PyTorch*) for index URLs, smoke test, and cache troubleshooting.  
- **Scripts** — `scripts/sync.py`, `scripts/embed.py`, `scripts/uv_torch_smoke.py`, `scripts/dashboard.py`.
