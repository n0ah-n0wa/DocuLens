"""The container image starts the worker with ``python -m doculens_worker``; keep that contract."""

import json
import os
import subprocess
import sys

import pytest

from doculens_worker import __version__

pytestmark = pytest.mark.unit


def test_module_entrypoint_runs_and_exits_zero() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "doculens_worker"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={
            **os.environ,
            "APP_ENV": "local",
            "LOG_FORMAT": "json",
            "DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:1/doculens",
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip(), completed.stderr
    entries = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    assert any(entry.get("version") == __version__ for entry in entries)
