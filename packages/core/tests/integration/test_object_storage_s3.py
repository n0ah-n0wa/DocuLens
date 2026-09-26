"""The S3 adapter against a real S3-compatible service (MinIO via testcontainers).

Skipped without Docker locally, required in CI. Set ``DOCULENS_TEST_S3_ENDPOINT_URL`` to reuse
the docker compose MinIO instead of starting a container.
"""

import os
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import SecretStr

from doculens.application.storage import ObjectStorage
from doculens.domain.storage import PDF_MIME_TYPE, StorageUnavailableError, content_hash
from doculens.infrastructure.config import CoreSettings, StorageEncryption
from doculens.infrastructure.storage import (
    ObjectStorageProbe,
    S3ObjectStorage,
    build_object_storage,
)
from doculens.testing.minio import (
    ObjectStoreUnavailableError,
    S3TestEnvironment,
    provisioned_s3,
    raw_client,
)
from doculens.testing.storage_contract import PAYLOAD, ObjectStorageContract, unique_key

pytestmark = pytest.mark.integration

DB_URL = SecretStr("postgresql+asyncpg://u:p@127.0.0.1:1/doculens")


@pytest.fixture(scope="session")
def s3_environment() -> Iterator[S3TestEnvironment]:
    try:
        with provisioned_s3() as environment:
            yield environment
    except ObjectStoreUnavailableError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(str(exc))


@pytest.fixture(scope="session")
def s3_settings(s3_environment: S3TestEnvironment) -> CoreSettings:
    return CoreSettings(_env_file=None, database_url=DB_URL, **s3_environment.settings_kwargs())


@pytest.fixture
def storage(s3_settings: CoreSettings) -> S3ObjectStorage:
    built = build_object_storage(s3_settings)
    assert isinstance(built, S3ObjectStorage)
    return built


@pytest.fixture
def raw(s3_environment: S3TestEnvironment) -> Any:  # noqa: ANN401 - stub-only client type
    return raw_client(s3_environment)


class TestS3ObjectStorage(ObjectStorageContract):
    @pytest.fixture(autouse=True)
    def _environment(self, s3_environment: S3TestEnvironment) -> None:
        self.environment = s3_environment

    async def corrupt_object(self, storage: ObjectStorage, key: str) -> None:
        """Replace the recorded hash behind the adapter's back (metadata-only copy)."""
        assert isinstance(storage, S3ObjectStorage)
        client = raw_client(self.environment)
        client.copy_object(
            Bucket=storage.bucket,
            Key=key,
            CopySource={"Bucket": storage.bucket, "Key": key},
            MetadataDirective="REPLACE",
            ContentType=PDF_MIME_TYPE,
            Metadata={"sha256": "0" * 64},
        )


async def test_the_service_stores_the_hash_as_object_metadata(
    storage: S3ObjectStorage,
    raw: Any,  # noqa: ANN401 - stub-only client type
    s3_environment: S3TestEnvironment,
) -> None:
    key = unique_key()

    await storage.put(key, PAYLOAD, content_type=PDF_MIME_TYPE, metadata={"origin": "test"})

    head = raw.head_object(Bucket=s3_environment.bucket, Key=key)
    assert head["Metadata"] == {"sha256": content_hash(PAYLOAD), "origin": "test"}
    assert head["ContentType"] == PDF_MIME_TYPE
    assert head["ContentLength"] == len(PAYLOAD)


async def test_bytes_altered_at_rest_fail_integrity_even_with_a_matching_size(
    storage: S3ObjectStorage,
    raw: Any,  # noqa: ANN401 - stub-only client type
    s3_environment: S3TestEnvironment,
) -> None:
    key = unique_key()
    await storage.put(key, PAYLOAD, content_type=PDF_MIME_TYPE)
    altered = PAYLOAD[:-1] + b"X"
    raw.put_object(
        Bucket=s3_environment.bucket,
        Key=key,
        Body=altered,
        ContentType=PDF_MIME_TYPE,
        Metadata={"sha256": content_hash(PAYLOAD)},
    )

    with pytest.raises(Exception, match="integrity"):
        await storage.get(key)


async def test_readiness_passes_against_the_live_store_and_fails_when_unreachable(
    storage: S3ObjectStorage, s3_settings: CoreSettings
) -> None:
    await ObjectStorageProbe(storage).check()

    unreachable = build_object_storage(
        s3_settings.model_copy(update={"storage_endpoint_url": "http://127.0.0.1:1"})
    )
    with pytest.raises(StorageUnavailableError):
        await ObjectStorageProbe(unreachable).check()


async def test_a_missing_bucket_is_unavailable_not_a_missing_object(
    s3_settings: CoreSettings,
) -> None:
    """GET carries the bucket error; HEAD answers a bodiless 404 no client can tell apart, which is
    why readiness uses ``head_bucket`` to catch a misconfigured bucket."""
    storage = build_object_storage(s3_settings.model_copy(update={"storage_bucket": "no-such-bkt"}))

    with pytest.raises(StorageUnavailableError):
        await storage.get(unique_key())
    with pytest.raises(StorageUnavailableError):
        await storage.check()


async def test_sse_s3_is_refused_by_a_store_without_kms_rather_than_silently_dropped(
    s3_settings: CoreSettings,
) -> None:
    """A store without KMS rejects SSE-S3 requests, which proves the header is really sent."""
    storage = build_object_storage(
        s3_settings.model_copy(update={"storage_encryption": StorageEncryption.AES256})
    )

    with pytest.raises(StorageUnavailableError):
        await storage.put(unique_key(), PAYLOAD, content_type=PDF_MIME_TYPE)
