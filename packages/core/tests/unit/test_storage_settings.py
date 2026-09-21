"""Storage configuration: safe local defaults, coherent S3 settings, strict when deployed."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from doculens.infrastructure.config import (
    CoreSettings,
    EmbeddingProviderKind,
    Environment,
    LLMProviderKind,
    StorageBackend,
    StorageEncryption,
)
from doculens.infrastructure.storage import (
    FilesystemObjectStorage,
    S3ObjectStorage,
    build_object_storage,
)

pytestmark = pytest.mark.unit

DB_URL = SecretStr("postgresql+asyncpg://u:p@127.0.0.1:1/doculens")


def _settings(**overrides: object) -> CoreSettings:
    return CoreSettings(_env_file=None, database_url=DB_URL, **overrides)  # type: ignore[arg-type]


def test_the_default_backend_is_the_filesystem_under_a_local_directory() -> None:
    settings = _settings()

    assert settings.storage_backend is StorageBackend.FILESYSTEM
    assert settings.storage_local_root == Path(".local/storage")
    assert settings.storage_encryption is StorageEncryption.NONE
    assert isinstance(build_object_storage(settings), FilesystemObjectStorage)


def test_the_s3_backend_needs_a_bucket_and_builds_without_touching_the_network() -> None:
    with pytest.raises(ValidationError, match="STORAGE_BUCKET is required"):
        _settings(storage_backend=StorageBackend.S3)

    settings = _settings(
        storage_backend=StorageBackend.S3,
        storage_bucket="doculens-documents",
        storage_region="eu-central-1",
        storage_endpoint_url="http://127.0.0.1:1",
        storage_access_key_id="local",
        storage_secret_access_key=SecretStr("local-secret"),
        storage_force_path_style=True,
    )

    storage = build_object_storage(settings)
    assert isinstance(storage, S3ObjectStorage)
    assert storage.bucket == "doculens-documents"
    assert "local-secret" not in repr(settings)


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"storage_bucket": "Bad_Bucket"}, "storage_bucket"),
        ({"storage_endpoint_url": "ftp://x"}, "http:// or https://"),
        ({"storage_access_key_id": "only-the-id"}, "set together"),
        ({"storage_encryption": StorageEncryption.AWS_KMS}, "STORAGE_KMS_KEY_ID is required"),
        ({"storage_kms_key_id": "alias/x"}, "needs STORAGE_ENCRYPTION=aws:kms"),
        ({"storage_endpoint_url": "http://user:pw@minio:9000"}, "must not embed credentials"),
        ({"storage_expected_bucket_owner": "12345"}, "storage_expected_bucket_owner"),
        ({"storage_max_object_bytes": 10}, "storage_max_object_bytes"),
    ],
)
def test_incoherent_storage_settings_are_rejected(
    overrides: dict[str, object], problem: str
) -> None:
    with pytest.raises(ValidationError, match=problem):
        _settings(**overrides)


DEPLOYED_S3 = {
    "storage_backend": StorageBackend.S3,
    "storage_bucket": "doculens-prod-documents",
    "storage_region": "eu-central-1",
    "storage_encryption": StorageEncryption.AES256,
    "embedding_provider": EmbeddingProviderKind.OPENAI,
    "llm_provider": LLMProviderKind.OPENAI,
    "chroma_url": "https://chroma.internal:8000",
}


@pytest.mark.parametrize("environment", [Environment.STAGING, Environment.PRODUCTION])
def test_deployed_environments_accept_encrypted_s3_with_role_credentials(
    environment: Environment,
) -> None:
    settings = _settings(app_env=environment, **DEPLOYED_S3)

    assert settings.storage_backend is StorageBackend.S3
    assert settings.storage_access_key_id is None


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"storage_backend": StorageBackend.FILESYSTEM}, "STORAGE_BACKEND must be s3"),
        ({"storage_encryption": StorageEncryption.NONE}, "STORAGE_ENCRYPTION must be"),
        ({"storage_endpoint_url": "http://minio:9000"}, "must use https"),
        ({"storage_region": None}, "STORAGE_REGION is required"),
        (
            {
                "storage_access_key_id": "AKIAEXAMPLE",
                "storage_secret_access_key": SecretStr("static"),
            },
            "STORAGE_ACCESS_KEY_ID must be unset",
        ),
    ],
)
def test_deployed_environments_reject_unsafe_storage(
    overrides: dict[str, object], problem: str
) -> None:
    with pytest.raises(ValidationError, match=problem):
        _settings(app_env=Environment.PRODUCTION, **{**DEPLOYED_S3, **overrides})


def test_ingestion_limits_default_to_the_spec_examples_and_fit_the_storage_cap() -> None:
    settings = _settings()

    assert settings.max_file_size_mb == 50
    assert settings.max_pages_per_document == 500
    assert settings.max_documents_per_user == 100
    assert settings.pdf_extraction_timeout_seconds == 120.0

    with pytest.raises(ValidationError, match="MAX_FILE_SIZE_MB must not exceed"):
        _settings(max_file_size_mb=200, storage_max_object_bytes=100 * 1024 * 1024)


def test_chunking_settings_default_and_stay_coherent() -> None:
    settings = _settings()

    assert (settings.chunk_size, settings.chunk_overlap, settings.min_chunk_size) == (512, 64, 64)

    with pytest.raises(ValidationError, match="CHUNK_OVERLAP must be smaller"):
        _settings(chunk_size=100, chunk_overlap=100)
    with pytest.raises(ValidationError, match="MIN_CHUNK_SIZE must not exceed"):
        _settings(chunk_size=100, min_chunk_size=101)


def test_the_per_page_text_cap_cannot_exceed_the_document_budget() -> None:
    with pytest.raises(ValidationError, match="PDF_MAX_CHARACTERS_PER_PAGE must not exceed"):
        _settings(pdf_max_characters_per_page=2_000_000, pdf_max_total_characters=1_000_000)
