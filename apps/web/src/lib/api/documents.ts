import type { Document, ListDocumentsParams, UpdateDocumentRequest } from "@doculens/shared-types";

import { apiRequest } from "@/lib/api/client";

function documentsQueryString(params: ListDocumentsParams = {}): string {
  const search = new URLSearchParams();
  if (params.collection_id !== undefined && params.collection_id.length > 0) {
    search.set("collection_id", params.collection_id);
  }
  if (params.q !== undefined && params.q.trim().length > 0) {
    search.set("q", params.q.trim());
  }
  const qs = search.toString();
  return qs.length > 0 ? `?${qs}` : "";
}

export async function listDocuments(
  params: ListDocumentsParams = {},
  signal?: AbortSignal,
): Promise<Document[]> {
  const options = signal === undefined ? {} : { signal };
  return apiRequest<Document[]>(`/api/v1/documents${documentsQueryString(params)}`, options);
}

export async function getDocument(documentId: string, signal?: AbortSignal): Promise<Document> {
  const options = signal === undefined ? {} : { signal };
  return apiRequest<Document>(`/api/v1/documents/${documentId}`, options);
}

export async function uploadDocument(input: {
  file: File;
  collectionId?: string | null;
}): Promise<Document> {
  const form = new FormData();
  form.append("file", input.file, input.file.name);
  if (input.collectionId) {
    form.append("collection_id", input.collectionId);
  }
  return apiRequest<Document>("/api/v1/documents", {
    method: "POST",
    body: form,
  });
}

export async function updateDocument(
  documentId: string,
  body: UpdateDocumentRequest,
): Promise<Document> {
  return apiRequest<Document>(`/api/v1/documents/${documentId}`, {
    method: "PATCH",
    body,
  });
}

export async function deleteDocument(documentId: string): Promise<void> {
  await apiRequest<null>(`/api/v1/documents/${documentId}`, {
    method: "DELETE",
  });
}

export async function reprocessDocument(documentId: string): Promise<Document> {
  return apiRequest<Document>(`/api/v1/documents/${documentId}/reprocess`, {
    method: "POST",
  });
}

export async function reindexDocument(documentId: string): Promise<Document> {
  return apiRequest<Document>(`/api/v1/documents/${documentId}/reindex`, {
    method: "POST",
  });
}
