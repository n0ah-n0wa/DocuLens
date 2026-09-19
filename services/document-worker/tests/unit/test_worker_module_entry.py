"""The container image starts the worker with ``python -m doculens_worker``; keep that contract."""

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
    )

    assert completed.returncode == 0, completed.stderr
    assert f"doculens-worker {__version__} started" in completed.stderr
