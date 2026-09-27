"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ListDocumentsParams, UpdateDocumentRequest } from "@doculens/shared-types";

import {
  deleteDocument,
  getDocument,
  listDocuments,
  reindexDocument,
  reprocessDocument,
  updateDocument,
  uploadDocument,
} from "@/lib/api/documents";
import { appConfig } from "@/lib/config";
import { shouldPollProcessingStatus } from "@/lib/documents/status";
import { queryKeys } from "@/lib/query-keys";

export function useDocumentsQuery(params: ListDocumentsParams = {}) {
  return useQuery({
    queryKey: queryKeys.documents.list(params),
    queryFn: ({ signal }) => listDocuments(params, signal),
    placeholderData: keepPreviousData,
    refetchIntervalInBackground: false,
    refetchInterval: (query) => {
      const docs = query.state.data;
      if (!docs?.some((doc) => shouldPollProcessingStatus(doc.processing_status))) {
        return false;
      }
      return appConfig.processingPollMs;
    },
  });
}

export function useDocumentQuery(documentId: string) {
  return useQuery({
    queryKey: queryKeys.documents.detail(documentId),
    queryFn: ({ signal }) => getDocument(documentId, signal),
    refetchIntervalInBackground: false,
    refetchInterval: (query) => {
      const doc = query.state.data;
      if (!doc || !shouldPollProcessingStatus(doc.processing_status)) {
        return false;
      }
      return appConfig.processingPollMs;
    },
  });
}

function invalidateDocumentQueries(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
}

export function useUploadDocumentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: uploadDocument,
    onSuccess: (document) => {
      queryClient.setQueryData(queryKeys.documents.detail(document.id), document);
      invalidateDocumentQueries(queryClient);
    },
  });
}

export function useUpdateDocumentMutation(documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: UpdateDocumentRequest) => updateDocument(documentId, body),
    onSuccess: (document) => {
      queryClient.setQueryData(queryKeys.documents.detail(document.id), document);
      invalidateDocumentQueries(queryClient);
    },
  });
}

export function useDeleteDocumentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteDocument,
    onMutate: async (documentId) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.documents.all });
      const previousLists = queryClient.getQueriesData({ queryKey: queryKeys.documents.all });
      const previousDetail = queryClient.getQueryData(queryKeys.documents.detail(documentId));
      queryClient.setQueriesData({ queryKey: ["documents", "list"] }, (current: unknown) => {
        if (!Array.isArray(current)) {
          return current;
        }
        return current.filter((doc: { id?: string }) => doc.id !== documentId);
      });
      queryClient.removeQueries({ queryKey: queryKeys.documents.detail(documentId) });
      return { previousLists, previousDetail, documentId };
    },
    onError: (_error, documentId, context) => {
      if (!context) {
        return;
      }
      for (const [key, data] of context.previousLists) {
        queryClient.setQueryData(key, data);
      }
      if (context.previousDetail !== undefined) {
        queryClient.setQueryData(queryKeys.documents.detail(documentId), context.previousDetail);
      }
    },
    onSettled: () => {
      invalidateDocumentQueries(queryClient);
    },
  });
}

export function useReprocessDocumentMutation(documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => reprocessDocument(documentId),
    onSuccess: (document) => {
      queryClient.setQueryData(queryKeys.documents.detail(document.id), document);
      invalidateDocumentQueries(queryClient);
    },
  });
}

export function useReindexDocumentMutation(documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => reindexDocument(documentId),
    onSuccess: (document) => {
      queryClient.setQueryData(queryKeys.documents.detail(document.id), document);
      invalidateDocumentQueries(queryClient);
    },
  });
}
