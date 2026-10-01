"""Hydrate process environment from AWS Secrets Manager (deployed runtimes).

Terraform injects ``APP_SECRETS_ARN`` (and optionally ``CHROMA_API_TOKEN_SECRET_ARN``)
instead of plaintext secrets. Call :func:`hydrate_secrets_into_environ` before settings
validation so ``JWT_SECRET`` / ``DATABASE_URL`` / ``REDIS_URL`` (and operator extras)
are available as environment variables.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from doculens.infrastructure.config import ConfigurationError

_HYDRATED = False


def hydrate_secrets_into_environ(*, force: bool = False) -> None:
    """Load Secrets Manager JSON into ``os.environ`` once per process.

    Existing environment variables win (explicit overrides / Lambda reserved names).
    No-op when ``APP_SECRETS_ARN`` is unset (local development).
    """
    global _HYDRATED  # noqa: PLW0603 - process-wide cold-start cache
    if _HYDRATED and not force:
        return

    app_arn = os.environ.get("APP_SECRETS_ARN", "").strip()
    if app_arn:
        payload = _get_secret_json(app_arn)
        _merge_into_environ(payload)

    chroma_arn = os.environ.get("CHROMA_API_TOKEN_SECRET_ARN", "").strip()
    if chroma_arn and not os.environ.get("CHROMA_API_TOKEN"):
        token_payload = _get_secret_value(chroma_arn)
        # Plain string secret or JSON with CHROMA_API_TOKEN / token / api_token.
        token = _extract_chroma_token(token_payload)
        if token:
            os.environ["CHROMA_API_TOKEN"] = token

    _HYDRATED = True


def reset_hydration_for_tests() -> None:
    """Allow unit tests to re-run hydration against a fresh environ."""
    global _HYDRATED  # noqa: PLW0603 - test-only reset
    _HYDRATED = False


def _get_secret_json(secret_id: str) -> dict[str, Any]:
    raw = _get_secret_value(secret_id)
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        message = f"APP_SECRETS_ARN secret must be a JSON object: {exc}"
        raise ConfigurationError(message) from exc
    if not isinstance(parsed, dict):
        message = "APP_SECRETS_ARN secret must be a JSON object"
        raise ConfigurationError(message)
    return parsed


def _get_secret_value(secret_id: str) -> str:
    try:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secret_id)
    except (BotoCoreError, ClientError) as exc:
        message = f"failed to read secret {secret_id}: {exc}"
        raise ConfigurationError(message) from exc
    secret_string = response.get("SecretString")
    if not isinstance(secret_string, str) or not secret_string:
        message = f"secret {secret_id} has no SecretString"
        raise ConfigurationError(message)
    return secret_string


def _merge_into_environ(payload: dict[str, Any]) -> None:
    for key, value in payload.items():
        if not key or key in os.environ or value is None:
            continue
        if isinstance(value, (dict, list)):
            os.environ[key] = json.dumps(value)
        else:
            os.environ[key] = str(value)


def _extract_chroma_token(raw: str) -> str | None:
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            parsed: object = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(parsed, dict):
            for key in ("CHROMA_API_TOKEN", "token", "api_token", "value"):
                candidate = parsed.get(key)
                if isinstance(candidate, str) and candidate:
                    return candidate
        return None
    return raw


__all__ = [
    "hydrate_secrets_into_environ",
    "reset_hydration_for_tests",
]
