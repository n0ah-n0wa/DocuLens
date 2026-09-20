"""Amazon S3 adapter for the ``ObjectStorage`` port (SPECIFICATIONS.md §11, §53, §57, §61).

Works against AWS S3 and against any S3-compatible service (MinIO locally) through the same
client. boto3 is synchronous, so every call runs on a worker thread; a semaphore bounds how many
transfers a process keeps in flight.

Security properties:

- objects are written with an explicit SHA-256 checksum the service verifies on receipt, and the
  same hash is recorded as object metadata and verified again on every download;
- objects larger than the configured maximum are refused on upload and, before any bytes are
  read, on download, so a foreign or corrupted object cannot exhaust memory;
- server-side encryption (SSE-S3 or SSE-KMS) is requested per object as configured; deployed
  environments refuse to run without it (see ``CoreSettings``);
- when an expected bucket owner (AWS account ID) is configured, every request carries it, so a
  bucket that changed hands or was re-created in another account is refused by the service;
- no ACL is ever set: bucket policy and IAM decide who can read, never the application;
- provider errors are translated: a missing object is ``ObjectNotFoundError``, anything else is
  ``StorageUnavailableError``; the provider's own message is logged, never returned to a client.
"""

import asyncio
import base64
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Self, cast

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from doculens.domain.storage import (
    CONTENT_HASH_METADATA_KEY,
    CONTENT_HASH_PATTERN,
    RESERVED_METADATA_KEYS,
    ObjectIntegrityError,
    ObjectMetadata,
    ObjectNotFoundError,
    StorageUnavailableError,
    StoredObject,
    content_hash,
    ensure_size_allowed,
    validate_content_type,
    validate_metadata,
    validate_object_key,
)
from doculens.infrastructure.config import CoreSettings, StorageEncryption

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import GetObjectOutputTypeDef, HeadObjectOutputTypeDef

logger = logging.getLogger(__name__)

PROBE_NAME = "object-storage"
NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
DEFAULT_MAX_OBJECT_BYTES = 100 * 1024 * 1024


def build_s3_client(settings: CoreSettings) -> "S3Client":
    """A client configured from settings: SigV4, bounded timeouts, standard retries."""
    config = BotoConfig(
        region_name=settings.storage_region,
        signature_version="s3v4",
        retries={"max_attempts": settings.storage_max_attempts, "mode": "standard"},
        connect_timeout=settings.storage_connect_timeout_seconds,
        read_timeout=settings.storage_read_timeout_seconds,
        s3={"addressing_style": "path" if settings.storage_force_path_style else "auto"},
        user_agent_extra="doculens",
    )
    secret = settings.storage_secret_access_key
    return boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint_url,
        aws_access_key_id=settings.storage_access_key_id,
        aws_secret_access_key=secret.get_secret_value() if secret is not None else None,
        config=config,
    )


class S3ObjectStorage:
    name = PROBE_NAME

    def __init__(  # noqa: PLR0913 - keyword-only settings mirror the STORAGE_* configuration
        self,
        client: "S3Client",
        *,
        bucket: str,
        encryption: StorageEncryption = StorageEncryption.NONE,
        kms_key_id: str | None = None,
        expected_bucket_owner: str | None = None,
        max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
        max_concurrent_transfers: int = 8,
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._encryption = encryption
        self._kms_key_id = kms_key_id
        self._expected_owner = expected_bucket_owner
        self._max_object_bytes = max_object_bytes
        self._slots = asyncio.Semaphore(max_concurrent_transfers)

    @classmethod
    def from_settings(cls, settings: CoreSettings) -> Self:
        if settings.storage_bucket is None:
            message = "STORAGE_BUCKET is required for the s3 storage backend"
            raise ValueError(message)
        return cls(
            build_s3_client(settings),
            bucket=settings.storage_bucket,
            encryption=settings.storage_encryption,
            kms_key_id=settings.storage_kms_key_id,
            expected_bucket_owner=settings.storage_expected_bucket_owner,
            max_object_bytes=settings.storage_max_object_bytes,
            max_concurrent_transfers=settings.storage_max_concurrent_transfers,
        )

    @property
    def bucket(self) -> str:
        return self._bucket

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectMetadata:
        validate_object_key(key)
        media_type = validate_content_type(content_type)
        custom = validate_metadata(metadata)
        ensure_size_allowed(len(data), self._max_object_bytes)
        digest = content_hash(data)
        request: dict[str, Any] = {
            **self._scope(),
            "Key": key,
            "Body": data,
            "ContentLength": len(data),
            "ContentType": media_type,
            "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode("ascii"),
            "Metadata": {CONTENT_HASH_METADATA_KEY: digest, **custom},
        }
        if self._encryption is StorageEncryption.AES256:
            request["ServerSideEncryption"] = "AES256"
        elif self._encryption is StorageEncryption.AWS_KMS:
            request["ServerSideEncryption"] = "aws:kms"
            if self._kms_key_id is not None:
                request["SSEKMSKeyId"] = self._kms_key_id
        await self._call(lambda: self._client.put_object(**request), key=key)
        logger.info(
            "object stored",
            extra={"operation": "storage.put", "object_key": key, "size": len(data)},
        )
        # The service's own timestamp would cost a second round trip; callers that need the
        # exact value use ``head``.
        return ObjectMetadata(
            key=key,
            size=len(data),
            content_type=media_type,
            content_hash=digest,
            last_modified=datetime.now(UTC),
            custom=custom,
        )

    async def get(self, key: str) -> StoredObject:
        validate_object_key(key)
        limit = self._max_object_bytes

        def read() -> "tuple[GetObjectOutputTypeDef, bytes]":
            response = self._client.get_object(**self._scope(), Key=key, ChecksumMode="ENABLED")
            body = response["Body"]
            try:
                # Refuse before reading: the declared length bounds memory, and the read below
                # is capped as well in case the declared length is wrong.
                ensure_size_allowed(int(response.get("ContentLength", 0)), limit)
                data = body.read(limit + 1)
                ensure_size_allowed(len(data), limit)
                return response, data
            finally:
                body.close()

        response, data = await self._call(read, key=key)
        metadata = _metadata_from(key, response, size=len(data))
        if metadata.content_hash is None or metadata.content_hash != content_hash(data):
            logger.error(
                "stored object failed its integrity check",
                extra={"operation": "storage.integrity", "object_key": key},
            )
            raise ObjectIntegrityError
        return StoredObject(data=data, metadata=metadata)

    async def head(self, key: str) -> ObjectMetadata | None:
        validate_object_key(key)
        try:
            response = await self._call(
                lambda: self._client.head_object(**self._scope(), Key=key), key=key
            )
        except ObjectNotFoundError:
            return None
        return _metadata_from(key, response, size=int(response.get("ContentLength", 0)))

    async def exists(self, key: str) -> bool:
        return await self.head(key) is not None

    async def delete(self, key: str) -> None:
        validate_object_key(key)
        await self._call(lambda: self._client.delete_object(**self._scope(), Key=key), key=key)
        logger.info("object deleted", extra={"operation": "storage.delete", "object_key": key})

    async def check(self) -> None:
        """Readiness: the bucket exists, has the expected owner and the credentials reach it."""
        await self._call(lambda: self._client.head_bucket(**self._scope()))

    def _scope(self) -> dict[str, Any]:
        """Bucket plus, when configured, the owner the service must verify on every request."""
        scope: dict[str, Any] = {"Bucket": self._bucket}
        if self._expected_owner is not None:
            scope["ExpectedBucketOwner"] = self._expected_owner
        return scope

    async def _call[T](self, operation: Callable[[], T], *, key: str | None = None) -> T:
        async with self._slots:
            try:
                return await asyncio.to_thread(operation)
            except ClientError as exc:
                raise _translate(exc, key) from exc
            except BotoCoreError as exc:
                logger.warning(
                    "object storage unreachable",
                    extra={"operation": "storage.error", "error_type": type(exc).__name__},
                )
                raise StorageUnavailableError from exc


def _translate(exc: ClientError, key: str | None) -> ObjectNotFoundError | StorageUnavailableError:
    error = exc.response.get("Error", {})
    code = str(error.get("Code", ""))
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    # A HEAD carries no body, so a missing bucket and a missing object both surface as "404";
    # readiness uses head_bucket to catch a misconfigured bucket.
    if key is not None and (code in NOT_FOUND_CODES or (status == 404 and code != "NoSuchBucket")):  # noqa: PLR2004
        return ObjectNotFoundError()
    logger.warning(
        "object storage request failed",
        extra={"operation": "storage.error", "error_code": code or "unknown", "status": status},
    )
    return StorageUnavailableError()


def _metadata_from(
    key: str, response: "GetObjectOutputTypeDef | HeadObjectOutputTypeDef", *, size: int
) -> ObjectMetadata:
    raw = dict(response.get("Metadata", {}))
    recorded = raw.get(CONTENT_HASH_METADATA_KEY)
    if recorded is not None and CONTENT_HASH_PATTERN.fullmatch(recorded) is None:
        recorded = None
    # Optional in practice (stubbed or partial responses) even though the stubs mark it required.
    last_modified = cast("datetime | None", response.get("LastModified")) or datetime.now(UTC)
    return ObjectMetadata(
        key=key,
        size=size,
        content_type=str(response.get("ContentType") or "application/octet-stream"),
        content_hash=recorded,
        last_modified=last_modified,
        custom={k: v for k, v in raw.items() if k not in RESERVED_METADATA_KEYS},
    )
