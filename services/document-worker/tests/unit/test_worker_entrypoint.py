import logging

import pytest

from doculens_worker import __version__
from doculens_worker.entrypoint import main

pytestmark = pytest.mark.unit


def test_main_exits_cleanly_and_logs_version(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="doculens_worker.entrypoint"):
        exit_code = main()

    assert exit_code == 0
    assert any(__version__ in record.getMessage() for record in caplog.records)
