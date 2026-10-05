import type {
  AnswerResponse,
  AskRequest,
  Conversation,
  CreateConversationRequest,
  Message,
  UpdateConversationRequest,
} from "@doculens/shared-types";

import { apiRequest, tryRefreshSession } from "@/lib/api/client";
import { ApiError, isApiErrorResponse } from "@/lib/api/errors";
import { appConfig } from "@/lib/config";
import { clearSessionTokens, getAccessToken, getRefreshToken } from "@/lib/auth/session";
import { consumeSseBuffer, parseAnswerStreamData } from "@/lib/chat/sse";

export async function listConversations(signal?: AbortSignal): Promise<Conversation[]> {
  const options = signal === undefined ? {} : { signal };
  return apiRequest<Conversation[]>("/api/v1/conversations", options);
}

export async function getConversation(
  conversationId: string,
  signal?: AbortSignal,
): Promise<Conversation> {
  const options = signal === undefined ? {} : { signal };
  return apiRequest<Conversation>(`/api/v1/conversations/${conversationId}`, options);
}

export async function createConversation(body: CreateConversationRequest): Promise<Conversation> {
  return apiRequest<Conversation>("/api/v1/conversations", {
    method: "POST",
    body,
  });
}

export async function updateConversation(
  conversationId: string,
  body: UpdateConversationRequest,
): Promise<Conversation> {
  return apiRequest<Conversation>(`/api/v1/conversations/${conversationId}`, {
    method: "PATCH",
    body,
  });
}

export async function deleteConversation(conversationId: string): Promise<void> {
  await apiRequest<null>(`/api/v1/conversations/${conversationId}`, {
    method: "DELETE",
  });
}

export async function listMessages(
  conversationId: string,
  signal?: AbortSignal,
): Promise<Message[]> {
  const options = signal === undefined ? {} : { signal };
  return apiRequest<Message[]>(`/api/v1/conversations/${conversationId}/messages`, options);
}

/** Synchronous ask (non-streaming). Prefer {@link askQuestionStream} for the chat UI. */
export async function askQuestion(
  conversationId: string,
  body: AskRequest,
): Promise<AnswerResponse> {
  return apiRequest<AnswerResponse>(`/api/v1/conversations/${conversationId}/messages`, {
    method: "POST",
    body,
  });
}

export type AskStreamHandlers = {
  onDelta?: (text: string) => void;
  signal?: AbortSignal;
};

/**
 * Ask via SSE. Progressive ``delta`` events are for display only; citations and persistence
 * arrive only with ``final``. On ``error``, abort, or connection loss, no messages were
 * persisted — discard any partial text.
 */
export async function askQuestionStream(
  conversationId: string,
  body: AskRequest,
  handlers: AskStreamHandlers = {},
): Promise<AnswerResponse> {
  const path = `/api/v1/conversations/${conversationId}/messages/stream`;
  const requestId =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `web-${Date.now().toString(36)}`;

  async function once(skipRefresh: boolean): Promise<AnswerResponse> {
    const headers: Record<string, string> = {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
      [appConfig.requestIdHeader]: requestId,
    };
    const token = getAccessToken();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }

    let response: Response;
    try {
      const init: RequestInit = {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      };
      if (handlers.signal !== undefined) {
        init.signal = handlers.signal;
      }
      response = await fetch(`${appConfig.apiBaseUrl}${path}`, init);
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") {
        throw cause;
      }
      if (cause instanceof Error && cause.name === "AbortError") {
        throw cause;
      }
      throw new ApiError(0, {
        code: "NETWORK_ERROR",
        message: "Unable to reach the DocuLens API.",
        request_id: requestId,
      });
    }

    if (response.status === 401 && !skipRefresh && getRefreshToken() !== null) {
      const refreshed = await tryRefreshSession();
      if (refreshed) {
        return once(true);
      }
      clearSessionTokens();
    } else if (response.status === 401) {
      clearSessionTokens();
    }

    if (!response.ok) {
      let payload: unknown = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
      if (isApiErrorResponse(payload)) {
        throw new ApiError(response.status, payload.error);
      }
      throw new ApiError(response.status, {
        code: "HTTP_ERROR",
        message: `Request failed with status ${response.status}.`,
        request_id: requestId,
      });
    }

    if (!response.body) {
      throw new ApiError(response.status, {
        code: "STREAM_EMPTY",
        message: "The answer stream returned no body.",
        request_id: requestId,
      });
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalAnswer: AnswerResponse | null = null;

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          buffer += decoder.decode();
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const consumed = consumeSseBuffer(buffer);
        buffer = consumed.rest;
        for (const raw of consumed.events) {
          const event = parseAnswerStreamData(raw);
          if (event === null) {
            continue;
          }
          if (event.type === "delta") {
            handlers.onDelta?.(event.text);
          } else if (event.type === "final") {
            finalAnswer = event.answer;
          } else if (event.type === "error") {
            throw new ApiError(0, event.error);
          }
        }
      }
      if (buffer.trim()) {
        const flushed = consumeSseBuffer(`${buffer}\n\n`);
        for (const raw of flushed.events) {
          const event = parseAnswerStreamData(raw);
          if (event === null) {
            continue;
          }
          if (event.type === "delta") {
            handlers.onDelta?.(event.text);
          } else if (event.type === "final") {
            finalAnswer = event.answer;
          } else if (event.type === "error") {
            throw new ApiError(0, event.error);
          }
        }
      }
    } finally {
      try {
        reader.releaseLock();
      } catch {
        // already released
      }
    }

    if (finalAnswer === null) {
      throw new ApiError(0, {
        code: "STREAM_INCOMPLETE",
        message: "The answer stream ended before a final response.",
        request_id: requestId,
      });
    }
    return finalAnswer;
  }

  return once(false);
}
