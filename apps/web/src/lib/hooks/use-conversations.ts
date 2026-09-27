"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  AskRequest,
  CreateConversationRequest,
  UpdateConversationRequest,
} from "@doculens/shared-types";

import {
  askQuestionStream,
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  listMessages,
  updateConversation,
  type AskStreamHandlers,
} from "@/lib/api/conversations";
import { queryKeys } from "@/lib/query-keys";

export function useConversationsQuery() {
  return useQuery({
    queryKey: queryKeys.conversations.all,
    queryFn: ({ signal }) => listConversations(signal),
  });
}

export function useConversationQuery(conversationId: string) {
  return useQuery({
    queryKey: queryKeys.conversations.detail(conversationId),
    queryFn: ({ signal }) => getConversation(conversationId, signal),
    enabled: conversationId.length > 0,
  });
}

export function useMessagesQuery(conversationId: string) {
  return useQuery({
    queryKey: queryKeys.conversations.messages(conversationId),
    queryFn: ({ signal }) => listMessages(conversationId, signal),
    enabled: conversationId.length > 0,
  });
}

export function useCreateConversationMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateConversationRequest) => createConversation(body),
    onSuccess: (conversation) => {
      queryClient.setQueryData(queryKeys.conversations.detail(conversation.id), conversation);
      void queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all });
    },
  });
}

export function useUpdateConversationMutation(conversationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: UpdateConversationRequest) => updateConversation(conversationId, body),
    onSuccess: (conversation) => {
      queryClient.setQueryData(queryKeys.conversations.detail(conversation.id), conversation);
      void queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all });
    },
  });
}

export function useDeleteConversationMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteConversation,
    onSuccess: (_void, conversationId) => {
      queryClient.removeQueries({ queryKey: queryKeys.conversations.detail(conversationId) });
      queryClient.removeQueries({ queryKey: queryKeys.conversations.messages(conversationId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all });
    },
  });
}

export type AskStreamVariables = {
  body: AskRequest;
  onDelta?: (text: string) => void;
  signal?: AbortSignal;
};

export function useAskQuestionMutation(conversationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ body, onDelta, signal }: AskStreamVariables) => {
      const handlers: AskStreamHandlers = {};
      if (onDelta !== undefined) {
        handlers.onDelta = onDelta;
      }
      if (signal !== undefined) {
        handlers.signal = signal;
      }
      return askQuestionStream(conversationId, body, handlers);
    },
    onSuccess: (answer) => {
      const targetId = answer.conversation_id;
      queryClient.setQueryData(queryKeys.conversations.messages(targetId), (current: unknown) => {
        if (!Array.isArray(current)) {
          return [answer.user_message, answer.assistant_message];
        }
        const ids = new Set(current.map((message: { id?: string }) => message.id).filter(Boolean));
        const next = [...current];
        if (!ids.has(answer.user_message.id)) {
          next.push(answer.user_message);
        }
        if (!ids.has(answer.assistant_message.id)) {
          next.push(answer.assistant_message);
        }
        return next;
      });
      void queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.conversations.detail(targetId),
      });
    },
  });
}
