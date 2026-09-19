from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from doculens_api.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A test client bound to a fresh application instance."""
    with TestClient(create_app()) as test_client:
        yield test_client
