"""Unit tests for Secrets Manager hydration used by deployed Lambdas."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from doculens.infrastructure.config import ConfigurationError
from doculens.infrastructure.secrets import (
    hydrate_secrets_into_environ,
    reset_hydration_for_tests,
)

pytestmark = pytest.mark.unit


class _FakeSecretsClient:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def get_secret_value(self, *, SecretId: str) -> dict[str, Any]:  # noqa: N803 - boto3 API
        if SecretId not in self._secrets:
            message = f"missing {SecretId}"
            raise RuntimeError(message)
        return {"SecretString": self._secrets[SecretId]}


def test_hydrate_merges_json_without_overriding_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_hydration_for_tests()
    monkeypatch.setenv("APP_SECRETS_ARN", "arn:aws:secretsmanager:eu-central-1:1:secret:app")
    monkeypatch.setenv("JWT_SECRET", "already-set")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    fake = _FakeSecretsClient(
        {
            "arn:aws:secretsmanager:eu-central-1:1:secret:app": json.dumps(
                {
                    "JWT_SECRET": "from-secret",
                    "DATABASE_URL": "postgresql+asyncpg://u:p@db/db",
                    "REDIS_URL": "rediss://redis:6379/0",
                }
            )
        }
    )
    monkeypatch.setattr(
        "doculens.infrastructure.secrets.boto3.client",
        lambda *_a, **_k: fake,
    )

    hydrate_secrets_into_environ(force=True)

    assert os.environ["JWT_SECRET"] == "already-set"  # noqa: S105 - fixture value
    assert os.environ["DATABASE_URL"] == "postgresql+asyncpg://u:p@db/db"
    assert os.environ["REDIS_URL"] == "rediss://redis:6379/0"


def test_hydrate_loads_chroma_token_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_hydration_for_tests()
    monkeypatch.delenv("APP_SECRETS_ARN", raising=False)
    monkeypatch.setenv(
        "CHROMA_API_TOKEN_SECRET_ARN",
        "arn:aws:secretsmanager:eu-central-1:1:secret:chroma",
    )
    monkeypatch.delenv("CHROMA_API_TOKEN", raising=False)
    fake = _FakeSecretsClient(
        {
            "arn:aws:secretsmanager:eu-central-1:1:secret:chroma": json.dumps(
                {"token": "chroma-token-value"}
            )
        }
    )
    monkeypatch.setattr(
        "doculens.infrastructure.secrets.boto3.client",
        lambda *_a, **_k: fake,
    )

    hydrate_secrets_into_environ(force=True)

    assert os.environ["CHROMA_API_TOKEN"] == "chroma-token-value"  # noqa: S105 - fixture value


def test_hydrate_rejects_non_object_app_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_hydration_for_tests()
    monkeypatch.setenv("APP_SECRETS_ARN", "arn:aws:secretsmanager:eu-central-1:1:secret:app")
    fake = _FakeSecretsClient(
        {"arn:aws:secretsmanager:eu-central-1:1:secret:app": json.dumps(["not", "an", "object"])}
    )
    monkeypatch.setattr(
        "doculens.infrastructure.secrets.boto3.client",
        lambda *_a, **_k: fake,
    )

    with pytest.raises(ConfigurationError, match="JSON object"):
        hydrate_secrets_into_environ(force=True)
