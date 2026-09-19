import uuid
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from doculens_api.main import create_app
from doculens_api.settings import ApiSettings

pytestmark = pytest.mark.api

UUID_VERSION_4 = 4


def test_every_response_carries_a_generated_request_id(client: TestClient) -> None:
    first = client.get("/health/live")
    second = client.get("/health/live")

    assert uuid.UUID(first.headers["X-Request-ID"]).version == UUID_VERSION_4
    assert uuid.UUID(second.headers["X-Request-ID"]).version == UUID_VERSION_4
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


def test_a_well_formed_incoming_request_id_is_honoured(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "gateway-abc.123_x"})

    assert response.headers["X-Request-ID"] == "gateway-abc.123_x"


@pytest.mark.parametrize("bad_value", ["", "has space", "x" * 129, "semi;colon", "a/b"])
def test_a_malformed_incoming_request_id_is_replaced(client: TestClient, bad_value: str) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": bad_value})

    returned = response.headers["X-Request-ID"]
    assert returned != bad_value
    assert uuid.UUID(returned).version == UUID_VERSION_4


def test_error_responses_carry_the_request_id_in_header_and_body(client: TestClient) -> None:
    response = client.get("/does-not-exist", headers={"X-Request-ID": "trace-42"})

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.headers["X-Request-ID"] == "trace-42"
    assert response.json()["error"]["request_id"] == "trace-42"


def test_the_header_name_is_configurable(settings: ApiSettings) -> None:
    custom = settings.model_copy(update={"request_id_header": "X-Correlation-Id"})

    with TestClient(create_app(custom)) as client:
        response = client.get("/health/live", headers={"X-Correlation-Id": "corr-1"})

    assert response.headers["X-Correlation-Id"] == "corr-1"
    assert "X-Request-ID" not in response.headers
