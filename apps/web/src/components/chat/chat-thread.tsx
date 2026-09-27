"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { AnswerOutcome, AskRequest, Citation, Message } from "@doculens/shared-types";

import { CitationPanel } from "@/components/chat/citation-panel";
import { MessageList } from "@/components/chat/message-list";
import { QuestionComposer } from "@/components/chat/question-composer";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui/query-state";
import { describeApiError, messageForApiError } from "@/lib/api/errors";
import { useCollectionsQuery } from "@/lib/hooks/use-collections";
import {
  useAskQuestionMutation,
  useConversationQuery,
  useMessagesQuery,
} from "@/lib/hooks/use-conversations";
import { useDocumentsQuery } from "@/lib/hooks/use-documents";
import { queryKeys } from "@/lib/query-keys";

export function ChatThreadPage({ conversationId }: { conversationId: string }) {
  const queryClient = useQueryClient();
  const conversationQuery = useConversationQuery(conversationId);
  const messagesQuery = useMessagesQuery(conversationId);
  const documentsQuery = useDocumentsQuery();
  const collectionsQuery = useCollectionsQuery();
  const askMutation = useAskQuestionMutation(conversationId);
  const abortRef = useRef<AbortController | null>(null);
  const activeConversationRef = useRef(conversationId);
  const askInFlightRef = useRef(false);

  const [activeCitationOrder, setActiveCitationOrder] = useState<number | null>(null);
  const [panelCitations, setPanelCitations] = useState<Citation[] | null>(null);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [askError, setAskError] = useState<string | null>(null);
  const [askErrorRequestId, setAskErrorRequestId] = useState<string | undefined>();
  const [lastOutcome, setLastOutcome] = useState<AnswerOutcome | null>(null);
  const [lastAsk, setLastAsk] = useState<{
    question: string;
    documentIds?: string[];
  } | null>(null);
  const [wasCancelled, setWasCancelled] = useState(false);
  const [citationsOpen, setCitationsOpen] = useState(false);

  useEffect(() => {
    activeConversationRef.current = conversationId;
  }, [conversationId]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, [conversationId]);

  const documentsById = useMemo(() => {
    const map = new Map(
      (documentsQuery.data ?? []).map((document) => [document.id, document] as const),
    );
    return map;
  }, [documentsQuery.data]);

  const readyDocuments = useMemo(
    () => (documentsQuery.data ?? []).filter((document) => document.processing_status === "READY"),
    [documentsQuery.data],
  );

  const messages = useMemo(() => messagesQuery.data ?? [], [messagesQuery.data]);
  const latestAssistantCitations = useMemo(() => latestCitations(messages), [messages]);
  const citationsForPanel =
    streamingText !== null ? [] : (panelCitations ?? latestAssistantCitations);
  const asking = askMutation.isPending || pendingQuestion !== null;

  function isStillActive(): boolean {
    return activeConversationRef.current === conversationId;
  }

  function selectCitation(order: number, citations?: Citation[]) {
    if (citations !== undefined) {
      setPanelCitations(citations);
    }
    setActiveCitationOrder(order);
    setCitationsOpen(true);
    queueMicrotask(() => {
      document.getElementById(`citation-${order}`)?.scrollIntoView({
        block: "nearest",
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      });
    });
  }

  function cancelStream() {
    abortRef.current?.abort();
  }

  async function runAsk(input: { question: string; documentIds?: string[] }) {
    if (askInFlightRef.current) {
      return;
    }
    askInFlightRef.current = true;
    setAskError(null);
    setAskErrorRequestId(undefined);
    setLastOutcome(null);
    setWasCancelled(false);
    setLastAsk(input);
    setPendingQuestion(input.question);
    setStreamingText("");
    setPanelCitations([]);
    setActiveCitationOrder(null);

    const controller = new AbortController();
    abortRef.current = controller;
    const requestConversationId = conversationId;

    const body: AskRequest = {
      question: input.question,
      ...(input.documentIds !== undefined ? { document_ids: input.documentIds } : {}),
    };

    try {
      const answer = await askMutation.mutateAsync({
        body,
        signal: controller.signal,
        onDelta: (text) => {
          if (activeConversationRef.current !== requestConversationId) {
            return;
          }
          setStreamingText((current) => (current ?? "") + text);
        },
      });
      if (activeConversationRef.current !== requestConversationId) {
        return;
      }
      setLastOutcome(answer.outcome);
      setPanelCitations(answer.assistant_message.citations);
      if (answer.assistant_message.citations.length > 0) {
        setActiveCitationOrder(answer.assistant_message.citations[0]?.citation_order ?? null);
        setCitationsOpen(true);
      } else {
        setActiveCitationOrder(null);
      }
    } catch (error) {
      if (activeConversationRef.current !== requestConversationId) {
        return;
      }
      const aborted =
        (error instanceof DOMException && error.name === "AbortError") ||
        (error instanceof Error && error.name === "AbortError");
      setStreamingText(null);
      setPanelCitations(null);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.conversations.messages(requestConversationId),
      });
      if (aborted) {
        setWasCancelled(true);
        return;
      }
      const described = describeApiError(error, "Could not get an answer.");
      setAskError(described.message);
      setAskErrorRequestId(described.requestId);
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      if (isStillActive() && activeConversationRef.current === requestConversationId) {
        setPendingQuestion(null);
        setStreamingText(null);
      }
      askInFlightRef.current = false;
    }
  }

  if (conversationQuery.isPending && conversationQuery.data === undefined) {
    return <LoadingState label="Loading conversation" />;
  }

  if (conversationQuery.isError || !conversationQuery.data) {
    return (
      <ErrorState
        title="Conversation unavailable"
        message={messageForApiError(conversationQuery.error, "The conversation was not found.")}
        action={
          <Link
            href="/chat"
            className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-900 hover:bg-slate-50"
          >
            Back to chat
          </Link>
        }
      />
    );
  }

  const conversation = conversationQuery.data;
  const collectionName = conversation.collection_id
    ? (collectionsQuery.data?.find((c) => c.id === conversation.collection_id)?.name ?? null)
    : null;
  const messagesFailed = messagesQuery.isError && messagesQuery.data === undefined;
  const messagesLoading = messagesQuery.isPending && messagesQuery.data === undefined;

  return (
    <div className="flex min-h-[min(70vh,40rem)] flex-col gap-4">
      <PageHeader
        title={conversation.title}
        description={
          conversation.collection_id
            ? collectionName
              ? `Default scope: collection “${collectionName}”.`
              : "Default scope: conversation collection."
            : "Default scope: all READY documents."
        }
        actions={
          <Button
            type="button"
            variant="secondary"
            className="xl:hidden"
            aria-expanded={citationsOpen}
            aria-controls="citations-panel"
            onClick={() => setCitationsOpen((open) => !open)}
          >
            {citationsOpen ? "Hide citations" : "Citations"}
            {citationsForPanel.length > 0 ? ` (${citationsForPanel.length})` : ""}
          </Button>
        }
      />

      {lastOutcome === "insufficient_evidence" ? (
        <Alert tone="info" title="Insufficient evidence">
          The library did not provide enough grounded evidence for a cited answer.
        </Alert>
      ) : null}
      {lastOutcome === "blocked" ? (
        <Alert title="Answer withheld">
          The model response could not be grounded safely, so it was blocked.
        </Alert>
      ) : null}
      {wasCancelled ? (
        <Alert tone="info" title="Stopped">
          The partial answer was discarded. The conversation was left unchanged.
        </Alert>
      ) : null}

      <div className="grid min-h-0 flex-1 gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(18rem,0.9fr)]">
        <div className="flex min-h-0 min-w-0 flex-col gap-4">
          {messagesLoading ? (
            <LoadingState label="Loading messages" />
          ) : messagesFailed ? (
            <ErrorState
              title="Could not load messages"
              message={messageForApiError(messagesQuery.error)}
              action={
                <Button variant="secondary" onClick={() => void messagesQuery.refetch()}>
                  Try again
                </Button>
              }
            />
          ) : (
            <div className="flex min-h-[16rem] flex-1 flex-col rounded-md border border-slate-200 bg-slate-50/40 p-3 sm:min-h-[20rem]">
              <MessageList
                messages={messages}
                activeCitationOrder={activeCitationOrder}
                onCitationClick={selectCitation}
                pendingQuestion={pendingQuestion}
                streamingAssistantText={streamingText}
              />
            </div>
          )}

          <div className="sticky bottom-0 z-10 -mx-1 bg-gradient-to-t from-white via-white to-white/80 px-1 pt-2 pb-1">
            <QuestionComposer
              busy={asking}
              error={askError}
              errorRequestId={askErrorRequestId}
              readyDocuments={readyDocuments}
              collections={collectionsQuery.data ?? []}
              conversationCollectionId={conversation.collection_id}
              onCancel={cancelStream}
              canRetry={Boolean(lastAsk) && !asking && askError !== null}
              onRetry={() => {
                if (lastAsk) {
                  void runAsk(lastAsk);
                }
              }}
              onAsk={async (input) => {
                await runAsk(input);
              }}
            />
          </div>

          {readyDocuments.length === 0 ? (
            <Alert tone="info" title="No READY documents">
              Upload and wait for processing to finish before asking, or wait for documents in the
              conversation collection to become READY.
            </Alert>
          ) : null}
        </div>

        <section
          id="citations-panel"
          className={`space-y-3 rounded-md border border-slate-200 bg-white p-3 ${
            citationsOpen ? "block" : "hidden"
          } xl:block`}
          aria-labelledby="citations-heading"
        >
          <div className="flex items-center justify-between gap-2">
            <h2 id="citations-heading" className="text-sm font-semibold text-slate-900">
              Citations
            </h2>
            <p className="text-xs text-slate-500">
              {streamingText !== null
                ? "Waiting for final answer"
                : panelCitations === null
                  ? "Latest assistant answer"
                  : "Selected answer"}
            </p>
          </div>
          <CitationPanel
            citations={citationsForPanel}
            documentsById={documentsById}
            activeOrder={activeCitationOrder}
            onSelect={(order) => selectCitation(order)}
          />
        </section>
      </div>
    </div>
  );
}

function latestCitations(messages: Message[]): Citation[] {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role === "ASSISTANT") {
      return message.citations;
    }
  }
  return [];
}
