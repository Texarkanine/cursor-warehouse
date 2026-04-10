#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""cursor-warehouse: thin hook launcher that forks sync + dashboard as detached processes.

Cursor hooks run synchronously with managed timeouts and kill on timeout.
Initial sync takes 10-30+ seconds — too slow for synchronous execution.
This launcher forks both processes fully detached and returns immediately.
"""

import socket
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PORT = 3141


def _detach_kwargs() -> dict:
    """Platform-specific kwargs to fully detach a child process."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def main():
    common = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, **_detach_kwargs()}

    subprocess.Popen(
        [sys.executable, str(SCRIPTS_DIR / "sync.py")],
        **common,
    )

    if not _port_in_use(PORT):
        subprocess.Popen(
            [sys.executable, str(SCRIPTS_DIR / "dashboard.py")],
            **common,
        )


if __name__ == "__main__":
    main()
