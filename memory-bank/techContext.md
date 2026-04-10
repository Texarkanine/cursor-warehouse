# Tech Context

Python-based analytics pipeline using standalone PEP 723 scripts. No package installation or pyproject.toml — each script self-declares dependencies.

## Environment Setup

Requires [uv](https://docs.astral.sh/uv/) (the Python package runner). No other global tools needed — `uv run --script` handles dependency resolution per-script.

## Build Tools

No build step. Scripts run directly via `uv run --script scripts/<name>.py`.

## Testing Process

Tests run with pytest via `uv run --with pytest --with duckdb pytest tests/ -v`. Test infrastructure is in `tests/conftest.py` (shared DuckDB fixtures, sys.path setup). Fixtures are in `tests/fixtures/` (sample JSONL files, tracking DB).
