"""An S3-compatible store for integration tests: an existing endpoint or a disposable MinIO.

``DOCULENS_TEST_S3_ENDPOINT_URL`` (with the optional ``DOCULENS_TEST_S3_ACCESS_KEY_ID``,
``DOCULENS_TEST_S3_SECRET_ACCESS_KEY`` and ``DOCULENS_TEST_S3_BUCKET``) selects a running
endpoint such as the docker compose MinIO; otherwise a testcontainers MinIO is started. The
bucket is created if missing and its contents belong to the tests.
"""

import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

MINIO_IMAGE = "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"
MINIO_PORT = 9000
MINIO_STARTUP_TIMEOUT_SECONDS = 60.0
TEST_ACCESS_KEY_ID = "doculens-test"
TEST_SECRET_ACCESS_KEY = "doculens-test-secret"  # noqa: S105 gitleaks:allow - throwaway container
TEST_BUCKET = "doculens-test"
TEST_REGION = "us-east-1"

# See doculens.testing.postgres for why the Ryuk reaper is disabled by default.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


class ObjectStoreUnavailableError(RuntimeError):
    """No S3-compatible store could be provided (typically: Docker is not running)."""


@dataclass(frozen=True, slots=True)
class S3TestEnvironment:
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    region: str = TEST_REGION

    def settings_kwargs(self) -> dict[str, Any]:
        """Keyword arguments that point ``CoreSettings`` at this store."""
        return {
            "storage_backend": "s3",
            "storage_bucket": self.bucket,
            "storage_region": self.region,
            "storage_endpoint_url": self.endpoint_url,
            "storage_access_key_id": self.access_key_id,
            "storage_secret_access_key": self.secret_access_key,
            "storage_force_path_style": True,
        }


def raw_client(environment: S3TestEnvironment) -> Any:  # noqa: ANN401 - boto3 client type is a stub-only class
    """A plain boto3 client for test set-up and for inspecting what the adapter wrote."""
    return boto3.client(
        "s3",
        endpoint_url=environment.endpoint_url,
        aws_access_key_id=environment.access_key_id,
        aws_secret_access_key=environment.secret_access_key,
        region_name=environment.region,
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def ensure_bucket(environment: S3TestEnvironment) -> None:
    try:
        raw_client(environment).create_bucket(Bucket=environment.bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            raise


@contextmanager
def provisioned_s3() -> Iterator[S3TestEnvironment]:
    configured = os.environ.get("DOCULENS_TEST_S3_ENDPOINT_URL")
    if configured:
        environment = S3TestEnvironment(
            endpoint_url=configured,
            access_key_id=os.environ.get("DOCULENS_TEST_S3_ACCESS_KEY_ID", "doculens"),
            secret_access_key=os.environ.get(
                "DOCULENS_TEST_S3_SECRET_ACCESS_KEY", "doculens-local-only"
            ),
            bucket=os.environ.get("DOCULENS_TEST_S3_BUCKET", TEST_BUCKET),
        )
        ensure_bucket(environment)
        yield environment
        return

    from testcontainers.core.container import DockerContainer  # noqa: PLC0415 - optional dependency
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy  # noqa: PLC0415

    container = (
        DockerContainer(MINIO_IMAGE)
        .with_env("MINIO_ROOT_USER", TEST_ACCESS_KEY_ID)
        .with_env("MINIO_ROOT_PASSWORD", TEST_SECRET_ACCESS_KEY)
        .with_command("server /data")
        .with_exposed_ports(MINIO_PORT)
        # The port mapping is published once the server listens; wait for its banner first.
        .waiting_for(
            LogMessageWaitStrategy("API: ").with_startup_timeout(int(MINIO_STARTUP_TIMEOUT_SECONDS))
        )
    )
    try:
        container.start()
    except Exception as exc:
        message = f"MinIO container unavailable (is Docker running?): {exc}"
        raise ObjectStoreUnavailableError(message) from exc
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(MINIO_PORT)
        environment = S3TestEnvironment(
            endpoint_url=f"http://{host}:{port}",
            access_key_id=TEST_ACCESS_KEY_ID,
            secret_access_key=TEST_SECRET_ACCESS_KEY,
            bucket=TEST_BUCKET,
        )
        _wait_until_live(environment.endpoint_url)
        ensure_bucket(environment)
        yield environment
    finally:
        container.stop()


def _wait_until_live(endpoint_url: str) -> None:
    deadline = time.monotonic() + MINIO_STARTUP_TIMEOUT_SECONDS
    url = f"{endpoint_url}/minio/health/live"
    while True:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - local test URL
                if response.status == 200:  # noqa: PLR2004
                    return
        except (urllib.error.URLError, OSError):
            pass
        if time.monotonic() > deadline:
            message = f"MinIO did not become healthy within {MINIO_STARTUP_TIMEOUT_SECONDS}s"
            raise ObjectStoreUnavailableError(message)
        time.sleep(0.5)
