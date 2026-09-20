"""Object keys, content hashing and metadata rules (§11, §49)."""

import hashlib
from uuid import UUID

import pytest

from doculens.domain.storage import (
    MAX_METADATA_ENTRIES,
    MAX_METADATA_VALUE_LENGTH,
    InvalidContentTypeError,
    InvalidObjectKeyError,
    InvalidObjectMetadataError,
    ObjectTooLargeError,
    content_hash,
    document_object_key,
    ensure_size_allowed,
    key_belongs_to,
    owner_of_key,
    validate_content_type,
    validate_metadata,
    validate_object_key,
)

pytestmark = pytest.mark.unit

OWNER = UUID("11111111-1111-4111-8111-111111111111")
DOCUMENT = UUID("22222222-2222-4222-8222-222222222222")


def test_document_keys_follow_the_generated_layout_and_never_contain_the_filename() -> None:
    key = document_object_key(OWNER, DOCUMENT)

    assert key == f"documents/{OWNER}/{DOCUMENT}/original.pdf"
    assert validate_object_key(key) == key


def test_content_hash_is_hex_sha256() -> None:
    data = b"%PDF-1.7 example"

    assert content_hash(data) == hashlib.sha256(data).hexdigest()
    assert len(content_hash(b"")) == 64
    assert content_hash(data) != content_hash(data + b"\n")


@pytest.mark.parametrize(
    "key",
    [
        "documents/a/b/original.pdf",
        "a",
        "A-Z_0.9/x",
        "a/" * 100 + "z",
    ],
)
def test_safe_keys_are_accepted(key: str) -> None:
    assert validate_object_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/absolute",
        "trailing/",
        "double//slash",
        "dot/./segment",
        "up/../escape",
        "..",
        ".hidden",
        "back\\slash",
        "white space",
        "tab\tchar",
        "new\nline",
        "unicode/é",
        "null\x00byte",
        "a" * 1025,
        "-leading-dash",
        "trailing-dot./x",
        "trailing-dash-/x",
        "documents/CON/x",
        "documents/nul.pdf",
        "documents/com1.tmp/x",
        "LPT9",
    ],
)
def test_unsafe_keys_are_rejected(key: str) -> None:
    with pytest.raises(InvalidObjectKeyError):
        validate_object_key(key)


def test_the_owner_is_recoverable_from_a_document_key_and_nothing_else() -> None:
    key = document_object_key(OWNER, DOCUMENT)

    assert owner_of_key(key) == OWNER
    assert key_belongs_to(key, OWNER)
    assert not key_belongs_to(key, DOCUMENT)
    for other in ("documents", f"documents/{OWNER}", f"other/{OWNER}/x/y", "documents/nope/x/y"):
        assert owner_of_key(other) is None
        assert not key_belongs_to(other, OWNER)


@pytest.mark.parametrize(
    ("given", "canonical"),
    [("application/pdf", "application/pdf"), (" Application/PDF ", "application/pdf")],
)
def test_content_types_are_canonicalised(given: str, canonical: str) -> None:
    assert validate_content_type(given) == canonical


@pytest.mark.parametrize(
    "content_type",
    [
        "",
        "pdf",
        "application/",
        "/pdf",
        "application/pdf; charset=x",
        "text/plain\r\nX: y",
        "a b/c",
    ],
)
def test_malformed_content_types_are_rejected(content_type: str) -> None:
    with pytest.raises(InvalidContentTypeError):
        validate_content_type(content_type)


def test_sizes_above_the_cap_are_rejected() -> None:
    ensure_size_allowed(10, 10)
    with pytest.raises(ObjectTooLargeError):
        ensure_size_allowed(11, 10)


def test_metadata_is_copied_when_valid() -> None:
    original = {"filename-hint": "report.pdf", "source": "upload"}

    cleaned = validate_metadata(original)

    assert cleaned == original
    assert cleaned is not original
    assert validate_metadata(None) == {}
    assert validate_metadata({}) == {}


@pytest.mark.parametrize(
    "metadata",
    [
        {"sha256": "reserved"},
        {"Upper": "x"},
        {"under_score": "x"},
        {"": "x"},
        {"k" * 65: "x"},
        {"ok": "café"},
        {"ok": "line\nbreak"},
        {"ok": "tab\t"},
        {"ok": "v" * (MAX_METADATA_VALUE_LENGTH + 1)},
        {f"k{i}": "x" for i in range(MAX_METADATA_ENTRIES + 1)},
        {f"k{i}": "v" * 200 for i in range(12)},
    ],
)
def test_unsafe_metadata_is_rejected(metadata: dict[str, str]) -> None:
    with pytest.raises(InvalidObjectMetadataError):
        validate_metadata(metadata)
