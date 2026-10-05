"""Smoke that the portfolio demo script stays importable and documented."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "demo_api_happy_path.py"


@pytest.mark.unit
def test_demo_api_happy_path_script_exposes_main_and_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert SCRIPT.is_file()
    # Load as a module without executing __main__.
    namespace = runpy.run_path(str(SCRIPT), run_name="not_main")
    main = namespace["main"]
    with pytest.raises(SystemExit) as exited:
        main(["--help"])
    assert exited.value.code == 0
    captured = capsys.readouterr()
    assert (
        "happy-path" in captured.out.lower() or "DocuLens" in captured.out or "API" in captured.out
    )
    # Keep sys.argv untouched for other tests.
    assert "--help" not in sys.argv
