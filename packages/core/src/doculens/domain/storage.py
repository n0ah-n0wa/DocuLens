"""Object storage concepts (SPECIFICATIONS.md §11, §49, §53).

The domain fixes what an object key looks like, how keys for document originals are generated,
which owner a key belongs to, how content is hashed, which content types and user metadata a
stored object may carry, and how large an object may be. Adapters in the infrastructure layer
only move bytes; every rule that matters for security lives here so it is the same for S3, for
the local filesystem store and for any future backend.

Object keys are generated from identifiers the system owns (§11): a user-controlled filename
never becomes part of a key, so a key can neither escape its prefix nor collide with another
user's objects, and the owner of every document object is recoverable from its key.
"""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from doculens.domain.errors import DependencyUnavailableError, DomainError, InvalidInputError
from doculens.domain.errors import NotFoundError as _NotFoundError

PDF_MIME_TYPE = "application/pdf"
ORIGINAL_OBJECT_NAME = "original.pdf"
DOCUMENTS_PREFIX = "documents"

HASH_ALGORITHM = "sha256"
CONTENT_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")

MAX_OBJECT_KEY_LENGTH = 1024
# A segment starts and ends with a letter or digit: no leading dots (``.``, ``..``, hidden
# files) and no trailing dots or dashes (which Windows filesystems silently strip).
_KEY_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
# Device names that Windows resolves regardless of directory or extension.
_RESERVED_SEGMENT_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(1, 10)}
    | {f"lpt{n}" for n in range(1, 10)}
)

# RFC 2045 type/subtype tokens without parameters; stored lower-case.
_CONTENT_TYPE_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$"
)

# User metadata travels in HTTP headers on S3 (``x-amz-meta-*``): keys are header tokens, values
# must be printable ASCII, and the whole set is limited to 2 KB by the service.
METADATA_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_METADATA_ENTRIES = 16
MAX_METADATA_VALUE_LENGTH = 256
MAX_METADATA_TOTAL_BYTES = 2048
CONTENT_HASH_METADATA_KEY = "sha256"
RESERVED_METADATA_KEYS = frozenset({CONTENT_HASH_METADATA_KEY})


class ObjectNotFoundError(_NotFoundError):
    code = "OBJECT_NOT_FOUND"
    default_message = "The stored object was not found."


class InvalidObjectKeyError(InvalidInputError):
    code = "INVALID_OBJECT_KEY"
    default_message = "The object key is not acceptable."


class InvalidObjectMetadataError(InvalidInputError):
    code = "INVALID_OBJECT_METADATA"
    default_message = "The object metadata is not acceptable."


class InvalidContentTypeError(InvalidInputError):
    code = "INVALID_CONTENT_TYPE"
    default_message = "The content type is not acceptable."


class ObjectTooLargeError(InvalidInputError):
    code = "OBJECT_TOO_LARGE"
    default_message = "The object exceeds the maximum allowed size."


class ObjectIntegrityError(DomainError):
    """The bytes read back do not match the hash recorded when the object was stored."""

    code = "OBJECT_INTEGRITY"
    default_message = "The stored object failed its integrity check."


class StorageUnavailableError(DependencyUnavailableError):
    code = "STORAGE_UNAVAILABLE"
    default_message = "Document storage is temporarily unavailable."


def content_hash(data: bytes) -> str:
    """Hex SHA-256 of ``data``; the value stored with every object and used for duplicates (§49)."""
    return hashlib.sha256(data).hexdigest()


def document_object_key(owner_id: UUID, document_id: UUID) -> str:
    """The key of a document's original file: ``documents/{user_id}/{document_id}/original.pdf``."""
    return f"{DOCUMENTS_PREFIX}/{owner_id}/{document_id}/{ORIGINAL_OBJECT_NAME}"


def owner_of_key(key: str) -> UUID | None:
    """The owner encoded in a document object key, or ``None`` for any other key shape."""
    segments = key.split("/")
    if len(segments) < 3 or segments[0] != DOCUMENTS_PREFIX:  # noqa: PLR2004 - prefix, owner, rest
        return None
    try:
        return UUID(segments[1])
    except ValueError:
        return None


def key_belongs_to(key: str, owner_id: UUID) -> bool:
    """True when ``key`` lies inside ``owner_id``'s prefix (the tenant boundary, §9, §11)."""
    return owner_of_key(key) == owner_id


def validate_object_key(key: str) -> str:
    """Accept only keys made of safe path segments.

    Segments start and end with a letter or digit and may contain ``.``, ``_`` or ``-`` in
    between, so ``.``, ``..``, hidden names, trailing dots, empty segments, leading slashes,
    backslashes, whitespace, control characters and Windows device names are all refused. This
    keeps a key from escaping its prefix on a filesystem store and from needing any escaping in
    a URL.
    """
    if not key or len(key) > MAX_OBJECT_KEY_LENGTH:
        raise InvalidObjectKeyError
    for segment in key.split("/"):
        if _KEY_SEGMENT_PATTERN.fullmatch(segment) is None:
            raise InvalidObjectKeyError
        if segment.split(".", 1)[0].lower() in _RESERVED_SEGMENT_NAMES:
            raise InvalidObjectKeyError
    return key


def validate_content_type(content_type: str) -> str:
    """Return the canonical (lower-case) media type or raise; parameters are not accepted."""
    canonical = content_type.strip().lower()
    if _CONTENT_TYPE_PATTERN.fullmatch(canonical) is None:
        raise InvalidContentTypeError
    return canonical


def validate_metadata(metadata: Mapping[str, str] | None) -> dict[str, str]:
    """Return a copy of ``metadata`` that any backend can store as-is, or raise."""
    if not metadata:
        return {}
    if len(metadata) > MAX_METADATA_ENTRIES:
        raise InvalidObjectMetadataError
    total = 0
    cleaned: dict[str, str] = {}
    for key, value in metadata.items():
        if METADATA_KEY_PATTERN.fullmatch(key) is None or key in RESERVED_METADATA_KEYS:
            raise InvalidObjectMetadataError
        if len(value) > MAX_METADATA_VALUE_LENGTH or not all(" " <= char <= "~" for char in value):
            raise InvalidObjectMetadataError
        total += len(key) + len(value)
        cleaned[key] = value
    if total > MAX_METADATA_TOTAL_BYTES:
        raise InvalidObjectMetadataError
    return cleaned


def ensure_size_allowed(size: int, max_bytes: int) -> None:
    if size > max_bytes:
        raise ObjectTooLargeError


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    """What a backend knows about a stored object without reading its bytes."""

    key: str
    size: int
    content_type: str
    content_hash: str | None
    last_modified: datetime
    custom: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoredObject:
    """A downloaded object whose bytes have been verified against the recorded hash."""

    data: bytes
    metadata: ObjectMetadata
