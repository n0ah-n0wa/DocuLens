# ADR-020 — Document upload HTTP transport

**Status:** Accepted (provisional, 2026-09-26)
**Specification references:** §10, §11, §12, §33 (`POST /api/v1/documents`), §35, §53
**Resolves provisionally:** OQ-3 (upload path; download still open)

## Context

§33 requires `POST /api/v1/documents`. Intake (`DocumentIntakeService`) already validates,
stores and registers uploads (ADR-015) and enqueues processing (ADR-011). OQ-3 left the HTTP
transport open: API Gateway and synchronous Lambda payload caps make a 50 MB default
unreachable through the API, while a presigned S3 upload inverts §12's validation-before-store
order.

## Decision

| Concern         | Decision                                                                                                                                                                                                                                     |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Transport       | Direct multipart `POST /api/v1/documents` with fields `file` (required PDF) and optional `collection_id`                                                                                                                                     |
| Cap             | Enforced by `MAX_FILE_SIZE_MB` (and `STORAGE_MAX_OBJECT_BYTES`); operators targeting Lambda must set the cap ≤ ~5 MiB until a presigned path exists                                                                                          |
| Authn / authz   | Bearer access token; the document is owned by the caller; a foreign `collection_id` is `COLLECTION_NOT_FOUND` (ADR-013)                                                                                                                      |
| Response        | `201` with the public `DocumentResponse` (`UPLOADED`); storage key and content hash are never exposed                                                                                                                                        |
| Errors          | Domain codes via the §35 envelope: `UNSUPPORTED_FILE_TYPE`, `INVALID_FILE_SIGNATURE`, `EMPTY_FILE`, `FILE_TOO_LARGE` (413), `DOCUMENT_LIMIT_REACHED`, `DUPLICATE_DOCUMENT` (409, `details` carries `existing_document_id`), validation `422` |
| Processing      | After commit, `DocumentJobDispatcher.enqueue_quietly` (ADR-011); enqueue failure does not fail the upload                                                                                                                                    |
| Download / view | Still open under OQ-3; this ADR does not add a download route                                                                                                                                                                                |

## Alternatives considered

- **A — Presigned PUT to quarantine.** Preferred for 50 MB on Lambda, but needs completion /
  S3-event wiring, policy constraints, and a §12 wording change. Deferred until ADR-008.
- **C — ECS-hosted API for large direct uploads.** Contradicts §5.4 Lambda as the API runtime.
- **Defer the §33 endpoint.** Leaves the REST surface incomplete and blocks end-to-end local /
  CI upload tests that the intake service already supports.

## Consequences

- `python-multipart` is an API dependency; FastAPI parses the upload.
- Production on API Gateway/Lambda must configure `MAX_FILE_SIZE_MB` within platform body
  limits; the 50 MB default remains valid for local and non-Lambda deployments.
- Presigned upload (option A) remains the upgrade path and will supersede or extend this ADR
  when ADR-008 lands; intake stays the single write path for both.
- OQ-3's download/view half stays open.
