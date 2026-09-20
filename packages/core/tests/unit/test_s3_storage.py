"""S3 adapter behaviour that does not need a real service: request shape and error translation.

botocore's ``Stubber`` answers the client without any network so the exact parameters sent to
S3 (checksums, metadata, encryption headers) and the mapping of provider failures can be asserted.
"""

import base64
import io
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from botocore.config import Config as BotoConfig
from botocore.exceptions import EndpointConnectionError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from doculens.domain.storage import (
    PDF_MIME_TYPE,
    ObjectIntegrityError,
    ObjectNotFoundError,
    ObjectTooLargeError,
    StorageUnavailableError,
    content_hash,
)
from doculens.infrastructure.config import StorageEncryption
from doculens.infrastructure.storage import S3ObjectStorage

pytestmark = pytest.mark.unit

BUCKET = "doculens-unit"
KEY = "documents/owner/document/original.pdf"
PAYLOAD = b"%PDF-1.7 stubbed"


@pytest.fixture
def client() -> Any:  # noqa: ANN401 - stub-only client type
    return boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="stub",
        aws_secret_access_key="stub",  # noqa: S106 gitleaks:allow - stubbed client, no network
        config=BotoConfig(retries={"max_attempts": 1, "mode": "standard"}),
    )


@pytest.fixture
def stubber(client: Any) -> Iterator[Stubber]:  # noqa: ANN401 - stub-only client type
    with Stubber(client) as stub:
        yield stub
        stub.assert_no_pending_responses()


def _body(data: bytes) -> StreamingBody:
    return StreamingBody(io.BytesIO(data), len(data))


async def test_put_sends_a_verified_checksum_hash_metadata_and_encryption(
    client: Any,  # noqa: ANN401 - stub-only client type
    stubber: Stubber,
) -> None:
    storage = S3ObjectStorage(
        client, bucket=BUCKET, encryption=StorageEncryption.AWS_KMS, kms_key_id="alias/doculens"
    )
    digest = content_hash(PAYLOAD)
    stubber.add_response(
        "put_object",
        {"ETag": '"x"'},
        expected_params={
            "Bucket": BUCKET,
            "Key": KEY,
            "Body": PAYLOAD,
            "ContentLength": len(PAYLOAD),
            "ContentType": PDF_MIME_TYPE,
            "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode("ascii"),
            "Metadata": {"sha256": digest, "filename-hint": "a.pdf"},
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": "alias/doculens",
        },
    )

    stored = await storage.put(
        KEY, PAYLOAD, content_type=PDF_MIME_TYPE, metadata={"filename-hint": "a.pdf"}
    )

    assert stored.content_hash == digest
    assert stored.size == len(PAYLOAD)


async def test_put_without_encryption_sends_no_sse_headers(
    client: Any,  # noqa: ANN401 - stub-only client type
    stubber: Stubber,
) -> None:
    storage = S3ObjectStorage(client, bucket=BUCKET)
    digest = content_hash(PAYLOAD)
    stubber.add_response(
        "put_object",
        {},
        expected_params={
            "Bucket": BUCKET,
            "Key": KEY,
            "Body": PAYLOAD,
            "ContentLength": len(PAYLOAD),
            "ContentType": PDF_MIME_TYPE,
            "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode("ascii"),
            "Metadata": {"sha256": digest},
        },
    )

    await storage.put(KEY, PAYLOAD, content_type=PDF_MIME_TYPE)


async def test_get_verifies_the_recorded_hash(
    client: Any,  # noqa: ANN401 - stub-only client type
    stubber: Stubber,
) -> None:
    storage = S3ObjectStorage(client, bucket=BUCKET)
    stubber.add_response(
        "get_object",
        {
            "Body": _body(PAYLOAD),
            "ContentType": PDF_MIME_TYPE,
            "ContentLength": len(PAYLOAD),
            "LastModified": datetime(2026, 1, 1, tzinfo=UTC),
            "Metadata": {"sha256": content_hash(PAYLOAD), "k": "v"},
        },
        expected_params={"Bucket": BUCKET, "Key": KEY, "ChecksumMode": "ENABLED"},
    )

    fetched = await storage.get(KEY)

    assert fetched.data == PAYLOAD
    assert fetched.metadata.content_hash == content_hash(PAYLOAD)
    assert dict(fetched.metadata.custom) == {"k": "v"}
    assert fetched.metadata.last_modified == datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("metadata", [{"sha256": "0" * 64}, {}, {"sha256": "not-hex"}])
async def test_get_rejects_objects_whose_hash_is_wrong_or_missing(
    client: Any,  # noqa: ANN401 - stub-only client type
    stubber: Stubber,
    metadata: dict[str, str],
) -> None:
    storage = S3ObjectStorage(client, bucket=BUCKET)
    stubber.add_response(
        "get_object",
        {"Body": _body(PAYLOAD), "ContentType": PDF_MIME_TYPE, "Metadata": metadata},
        expected_params={"Bucket": BUCKET, "Key": KEY, "ChecksumMode": "ENABLED"},
    )

    with pytest.raises(ObjectIntegrityError):
        await storage.get(KEY)


async def test_missing_objects_map_to_not_found(
    client: Any,  # noqa: ANN401 - stub-only client type
    stubber: Stubber,
) -> None:
    storage = S3ObjectStorage(client, bucket=BUCKET)
    stubber.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
    stubber.add_client_error("head_object", service_error_code="404", http_status_code=404)

    with pytest.raises(ObjectNotFoundError):
        await storage.get(KEY)
    assert await storage.head(KEY) is None


@pytest.mark.parametrize(
    ("code", "status"),
    [("NoSuchBucket", 404), ("AccessDenied", 403), ("SlowDown", 503), ("InternalError", 500)],
)
async def test_other_provider_errors_are_reported_as_unavailable_without_details(
    client: Any,  # noqa: ANN401 - stub-only client type
    stubber: Stubber,
    code: str,
    status: int,
) -> None:
    storage = S3ObjectStorage(client, bucket=BUCKET)
    stubber.add_client_error(
        "head_object",
        service_error_code=code,
        service_message="provider detail",
        http_status_code=status,
    )

    with pytest.raises(StorageUnavailableError) as excinfo:
        await storage.head(KEY)

    assert "provider detail" not in str(excinfo.value)
    assert excinfo.value.code == "STORAGE_UNAVAILABLE"


async def test_network_failures_are_reported_as_unavailable(
    client: Any,  # noqa: ANN401 - stub-only client type
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = S3ObjectStorage(client, bucket=BUCKET)

    def unreachable(**_: object) -> None:
        raise EndpointConnectionError(endpoint_url="http://127.0.0.1:1")

    monkeypatch.setattr(client, "head_bucket", unreachable)

    with pytest.raises(StorageUnavailableError):
        await storage.check()


async def test_the_expected_bucket_owner_is_asserted_on_every_request(
    client: Any,  # noqa: ANN401 - stub-only client type
    stubber: Stubber,
) -> None:
    storage = S3ObjectStorage(client, bucket=BUCKET, expected_bucket_owner="123456789012")
    scope = {"Bucket": BUCKET, "ExpectedBucketOwner": "123456789012"}
    digest = content_hash(PAYLOAD)
    stubber.add_response(
        "put_object",
        {},
        expected_params={
            **scope,
            "Key": KEY,
            "Body": PAYLOAD,
            "ContentLength": len(PAYLOAD),
            "ContentType": PDF_MIME_TYPE,
            "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode("ascii"),
            "Metadata": {"sha256": digest},
        },
    )
    stubber.add_response(
        "get_object",
        {"Body": _body(PAYLOAD), "ContentType": PDF_MIME_TYPE, "Metadata": {"sha256": digest}},
        expected_params={**scope, "Key": KEY, "ChecksumMode": "ENABLED"},
    )
    stubber.add_response(
        "head_object", {"ContentLength": len(PAYLOAD)}, expected_params={**scope, "Key": KEY}
    )
    stubber.add_response("delete_object", {}, expected_params={**scope, "Key": KEY})
    stubber.add_response("head_bucket", {}, expected_params=scope)

    await storage.put(KEY, PAYLOAD, content_type=PDF_MIME_TYPE)
    await storage.get(KEY)
    await storage.head(KEY)
    await storage.delete(KEY)
    await storage.check()


async def test_oversized_objects_are_refused_before_their_body_is_read(
    client: Any,  # noqa: ANN401 - stub-only client type
    stubber: Stubber,
) -> None:
    storage = S3ObjectStorage(client, bucket=BUCKET, max_object_bytes=1024)
    huge = 1024 * 1024
    stubber.add_response(
        "get_object",
        {"Body": _body(b"x"), "ContentLength": huge, "Metadata": {}},
        expected_params={"Bucket": BUCKET, "Key": KEY, "ChecksumMode": "ENABLED"},
    )

    with pytest.raises(ObjectTooLargeError):
        await storage.get(KEY)
    with pytest.raises(ObjectTooLargeError):
        await storage.put(KEY, b"x" * 1025, content_type=PDF_MIME_TYPE)


async def test_a_body_longer_than_declared_is_still_capped(
    client: Any,  # noqa: ANN401 - stub-only client type
    stubber: Stubber,
) -> None:
    storage = S3ObjectStorage(client, bucket=BUCKET, max_object_bytes=8)
    stubber.add_response(
        "get_object",
        {"Body": _body(b"0123456789"), "ContentLength": 4, "Metadata": {}},
        expected_params={"Bucket": BUCKET, "Key": KEY, "ChecksumMode": "ENABLED"},
    )

    with pytest.raises(ObjectTooLargeError):
        await storage.get(KEY)


async def test_delete_is_idempotent_at_the_provider(
    client: Any,  # noqa: ANN401 - stub-only client type
    stubber: Stubber,
) -> None:
    storage = S3ObjectStorage(client, bucket=BUCKET)
    stubber.add_response("delete_object", {}, expected_params={"Bucket": BUCKET, "Key": KEY})

    await storage.delete(KEY)
