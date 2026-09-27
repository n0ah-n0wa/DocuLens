import type {
  Collection,
  CreateCollectionRequest,
  UpdateCollectionRequest,
} from "@doculens/shared-types";

import { apiRequest } from "@/lib/api/client";

export async function listCollections(signal?: AbortSignal): Promise<Collection[]> {
  const options = signal === undefined ? {} : { signal };
  return apiRequest<Collection[]>("/api/v1/collections", options);
}

export async function getCollection(
  collectionId: string,
  signal?: AbortSignal,
): Promise<Collection> {
  const options = signal === undefined ? {} : { signal };
  return apiRequest<Collection>(`/api/v1/collections/${collectionId}`, options);
}

export async function createCollection(body: CreateCollectionRequest): Promise<Collection> {
  return apiRequest<Collection>("/api/v1/collections", {
    method: "POST",
    body,
  });
}

export async function updateCollection(
  collectionId: string,
  body: UpdateCollectionRequest,
): Promise<Collection> {
  return apiRequest<Collection>(`/api/v1/collections/${collectionId}`, {
    method: "PATCH",
    body,
  });
}

export async function deleteCollection(collectionId: string): Promise<void> {
  await apiRequest<null>(`/api/v1/collections/${collectionId}`, {
    method: "DELETE",
  });
}
