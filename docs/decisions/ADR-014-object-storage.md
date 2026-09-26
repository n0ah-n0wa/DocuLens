# ADR-014 — Object storage adapters

**Status:** Accepted (2026-09-20) · **Resolves:** OQ-13 (local S3-compatible store) · **Refs:** §11,
§31, §49, §53, §57, §61, §67, §72, §87.

## Context

Original PDFs must live in S3 under generated keys (§11); the domain and application layers must
not depend on cloud SDKs (§72); local development must run without AWS (§87); integration tests
need an S3-compatible service (§61); stored objects must be encrypted at rest and never public
(§53, §57); an unavailable store must make uploads fail safely (§67); content hashes drive
duplicate detection (§49). OQ-13 asked which local S3-compatible service to use.

## Decision

| Concern             | Decision                                                                                                                                                                                                                                                                                     |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Port                | `doculens.application.storage.ObjectStorage`: `put`, `get`, `head`, `exists`, `delete`; async; raises domain errors only                                                                                                                                                                     |
| Domain rules        | `doculens.domain.storage`: key grammar (safe segments, no `.`/`..`, ≤ 1024 chars), generated keys `documents/{user_id}/{document_id}/original.pdf`, SHA-256 content hash, user-metadata grammar (S3 header constraints), `ObjectMetadata`, `StoredObject`                                    |
| Production adapter  | `S3ObjectStorage` on boto3 (synchronous client on worker threads, bounded by a semaphore); works for AWS S3 and any S3-compatible endpoint                                                                                                                                                   |
| Local adapter       | `FilesystemObjectStorage`: objects and JSON metadata under a root directory, atomic writes; used without Docker and by unit tests; refused when deployed                                                                                                                                     |
| Local S3-compatible | MinIO via Chainguard (`cgr.dev/chainguard/minio`, digest-pinned) in docker compose with a bucket-init job; the same image via testcontainers for integration tests; `DOCULENS_TEST_S3_ENDPOINT_URL` reuses a running one. Official MinIO Hub/Quay images are no longer anonymously pullable. |
| Integrity           | SHA-256 sent as the upload checksum (verified by the service) and recorded as object metadata `sha256`; verified again on every download; a mismatch is `ObjectIntegrityError`                                                                                                               |
| Encryption          | `STORAGE_ENCRYPTION` selects none (local), `AES256` (SSE-S3) or `aws:kms` with `STORAGE_KMS_KEY_ID`; deployed environments refuse `none`, refuse non-https custom endpoints and refuse static access keys (the execution role provides credentials)                                          |
| Access              | The adapter never sets ACLs or produces public URLs; bucket policy, public-access block and IAM are Terraform concerns (ADR-008)                                                                                                                                                             |
| Errors              | Missing object → `ObjectNotFoundError` (404); any other provider or network failure → `StorageUnavailableError` (503); provider messages are logged, never returned                                                                                                                          |
| Tenant isolation    | Request handlers only receive an `OwnerScopedObjectStorage` bound to the authenticated user: keys outside `documents/{user_id}/` read as missing and cannot be written; the raw adapter never leaves the composition root                                                                    |
| Key and type rules  | Keys: safe segments only, no leading or trailing dots, no Windows device names; content types: canonical `type/subtype` tokens without parameters; user metadata: header-safe tokens                                                                                                         |
| Size cap            | `STORAGE_MAX_OBJECT_BYTES` (100 MiB default) is enforced on upload and, from the declared length before any byte is read, on download                                                                                                                                                        |
| Bucket ownership    | `STORAGE_EXPECTED_BUCKET_OWNER` (AWS account ID) is sent as `ExpectedBucketOwner` on every request when set; `STORAGE_REGION` is mandatory when deployed so signing never guesses a region                                                                                                   |
| Readiness           | `ObjectStorageProbe` (`head_bucket` for S3, writable root for the filesystem) is part of `/health/ready`                                                                                                                                                                                     |
| Concurrency         | Payloads are passed as bytes; the API enforces the upload size limit before storage is involved, so memory per request is bounded by configuration (§10.2)                                                                                                                                   |

## Alternatives considered

- **aiobotocore / aioboto3.** Native async, but a second SDK pinned to exact botocore versions and
  a larger dependency surface; boto3 on a worker thread is simpler, Lambda-friendly and enough
  for one object per request.
- **LocalStack.** Higher AWS fidelity (SQS, Secrets Manager) at a much larger image; MinIO is
  lighter and exercises the same S3 API. LocalStack can be introduced later for queue tests if
  needed (OQ-13 note).
- **Filesystem adapter only for local development.** It would leave the S3 adapter untested
  outside AWS; the contract suite runs against both backends.
- **Presigned upload/download URLs.** Not implemented: whether uploads go through the API or
  straight to S3, and how documents are viewed, is `OQ-3` (ADR-008).

## Consequences

- New runtime dependency `boto3` in `doculens-core` and `boto3-stubs[s3]` for type checking.
- The compose stack gains `minio` and `minio-init`; `.env.example` documents `STORAGE_*`.
- Integration tests need Docker (skipped locally without it, required in CI), as for PostgreSQL.
- Upload validation, quotas and the document upload use case are the next step of Phase 3 and
  build on this port; PDF parsing is not part of this decision.
- Duplicate-upload detection (§49, `OQ-6`) can use the recorded `sha256` without reading objects.
- `delete` removes the current object only. The Terraform bucket must therefore keep versioning
  off, or pair it with a lifecycle rule that expires noncurrent versions promptly, for §31's
  "deletion removes the S3 object" to hold; the public-access block and default encryption are
  bucket settings owned by the storage module (ADR-008).
- Download responses (when `OQ-3` is decided) must serve the stored content type with
  `Content-Disposition: attachment` and `X-Content-Type-Options: nosniff`; the adapter reports
  the type it stored and never trusts a client-supplied one.
