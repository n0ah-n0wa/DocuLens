"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { CreateCollectionRequest, UpdateCollectionRequest } from "@doculens/shared-types";

import {
  createCollection,
  deleteCollection,
  getCollection,
  listCollections,
  updateCollection,
} from "@/lib/api/collections";
import { queryKeys } from "@/lib/query-keys";

export function useCollectionsQuery() {
  return useQuery({
    queryKey: queryKeys.collections.all,
    queryFn: ({ signal }) => listCollections(signal),
  });
}

export function useCollectionQuery(collectionId: string) {
  return useQuery({
    queryKey: queryKeys.collections.detail(collectionId),
    queryFn: ({ signal }) => getCollection(collectionId, signal),
  });
}

export function useCreateCollectionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateCollectionRequest) => createCollection(body),
    onSuccess: (collection) => {
      queryClient.setQueryData(queryKeys.collections.detail(collection.id), collection);
      void queryClient.invalidateQueries({ queryKey: queryKeys.collections.all });
    },
  });
}

export function useUpdateCollectionMutation(collectionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: UpdateCollectionRequest) => updateCollection(collectionId, body),
    onMutate: async (body) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.collections.detail(collectionId) });
      await queryClient.cancelQueries({ queryKey: queryKeys.collections.all });
      const previous = queryClient.getQueryData(queryKeys.collections.detail(collectionId));
      const previousList = queryClient.getQueryData(queryKeys.collections.all);
      queryClient.setQueryData(queryKeys.collections.detail(collectionId), (current: unknown) => {
        if (!current || typeof current !== "object") {
          return current;
        }
        return {
          ...current,
          ...(body.name !== undefined ? { name: body.name } : {}),
          ...(body.description !== undefined ? { description: body.description } : {}),
        };
      });
      queryClient.setQueryData(queryKeys.collections.all, (current: unknown) => {
        if (!Array.isArray(current)) {
          return current;
        }
        return current.map((item: { id?: string }) => {
          if (item.id !== collectionId) {
            return item;
          }
          return {
            ...item,
            ...(body.name !== undefined ? { name: body.name } : {}),
            ...(body.description !== undefined ? { description: body.description } : {}),
          };
        });
      });
      return { previous, previousList };
    },
    onError: (_error, _body, context) => {
      if (context?.previous !== undefined) {
        queryClient.setQueryData(queryKeys.collections.detail(collectionId), context.previous);
      }
      if (context?.previousList !== undefined) {
        queryClient.setQueryData(queryKeys.collections.all, context.previousList);
      }
    },
    onSuccess: (collection) => {
      queryClient.setQueryData(queryKeys.collections.detail(collection.id), collection);
      void queryClient.invalidateQueries({ queryKey: queryKeys.collections.all });
    },
  });
}

export function useDeleteCollectionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteCollection,
    onSuccess: (_void, collectionId) => {
      queryClient.removeQueries({ queryKey: queryKeys.collections.detail(collectionId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.collections.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
    },
  });
}
