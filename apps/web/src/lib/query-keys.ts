import type { ListDocumentsParams } from "@doculens/shared-types";

export const queryKeys = {
  collections: {
    all: ["collections"] as const,
    detail: (id: string) => ["collections", id] as const,
  },
  documents: {
    all: ["documents"] as const,
    list: (params: ListDocumentsParams = {}) =>
      ["documents", "list", params.collection_id ?? null, params.q ?? null] as const,
    detail: (id: string) => ["documents", id] as const,
  },
  conversations: {
    all: ["conversations"] as const,
    detail: (id: string) => ["conversations", id] as const,
    messages: (id: string) => ["conversations", id, "messages"] as const,
  },
} as const;
