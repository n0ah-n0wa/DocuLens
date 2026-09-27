"use client";

import { useEffect, useRef } from "react";
import type { Citation, Message } from "@doculens/shared-types";

import { citationsByOrder, splitAnswerWithCitations } from "@/lib/chat/citations";
import { formatTimestamp } from "@/lib/format";

export function MessageList({
  messages,
  activeCitationOrder,
  onCitationClick,
  pendingQuestion,
  streamingAssistantText,
}: {
  messages: Message[];
  activeCitationOrder: number | null;
  onCitationClick: (order: number, citations: Citation[]) => void;
  pendingQuestion?: string | null;
  /** Progressive assistant text; citations are not rendered until the stream finalises. */
  streamingAssistantText?: string | null;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  const isStreaming =
    pendingQuestion !== null &&
    pendingQuestion !== undefined &&
    streamingAssistantText !== null &&
    streamingAssistantText !== undefined;

  useEffect(() => {
    if (!pendingQuestion && streamingAssistantText === null) {
      return;
    }
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    endRef.current?.scrollIntoView({
      block: "nearest",
      behavior: reduceMotion ? "auto" : "smooth",
    });
  }, [pendingQuestion, streamingAssistantText, messages.length]);

  if (messages.length === 0 && !pendingQuestion && !streamingAssistantText) {
    return (
      <div
        className="flex min-h-48 items-center justify-center rounded-md border border-dashed border-slate-200 bg-white px-4 text-sm text-slate-600"
        role="status"
      >
        Ask a question to start this conversation. Answers include separate citation evidence.
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain pr-1"
        tabIndex={0}
        aria-label="Message history"
      >
        <ol className="space-y-4">
          {messages.map((message) => (
            <li key={message.id}>
              {message.role === "USER" ? (
                <UserBubble content={message.content} createdAt={message.created_at} />
              ) : message.role === "ASSISTANT" ? (
                <AssistantBubble
                  content={message.content}
                  createdAt={message.created_at}
                  citations={message.citations}
                  activeCitationOrder={activeCitationOrder}
                  onCitationClick={onCitationClick}
                />
              ) : (
                <SystemBubble content={message.content} createdAt={message.created_at} />
              )}
            </li>
          ))}
          {pendingQuestion ? (
            <li>
              <UserBubble content={pendingQuestion} createdAt={new Date().toISOString()} pending />
              {isStreaming ? (
                <div
                  className="mt-3 max-w-3xl rounded-md border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900"
                  aria-busy="true"
                >
                  <p className="text-xs font-medium tracking-wide text-slate-500 uppercase">
                    Assistant
                  </p>
                  {/* Streaming body is not aria-live — only the status line announces. */}
                  <p className="mt-2 max-h-[40vh] overflow-y-auto whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
                    {streamingAssistantText.length > 0 ? streamingAssistantText : null}
                  </p>
                  <p className="mt-2 text-xs text-slate-500" role="status" aria-live="polite">
                    {streamingAssistantText.length > 0
                      ? "Generating answer… Citations appear when complete."
                      : "Generating a grounded answer…"}
                  </p>
                </div>
              ) : (
                <div
                  className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600"
                  role="status"
                  aria-live="polite"
                >
                  Generating a grounded answer…
                </div>
              )}
            </li>
          ) : null}
        </ol>
        <div ref={endRef} aria-hidden="true" />
      </div>
    </div>
  );
}

function UserBubble({
  content,
  createdAt,
  pending = false,
}: {
  content: string;
  createdAt: string;
  pending?: boolean;
}) {
  return (
    <div className="ml-auto max-w-3xl rounded-md bg-slate-900 px-4 py-3 text-sm text-white">
      <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{content}</p>
      <p className="mt-2 text-xs text-slate-300">
        {pending ? "Sending…" : formatTimestamp(createdAt)}
      </p>
    </div>
  );
}

function SystemBubble({ content, createdAt }: { content: string; createdAt: string }) {
  return (
    <div className="max-w-3xl rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
      <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{content}</p>
      <p className="mt-2 text-xs text-slate-500">{formatTimestamp(createdAt)}</p>
    </div>
  );
}

function AssistantBubble({
  content,
  createdAt,
  citations,
  activeCitationOrder,
  onCitationClick,
}: {
  content: string;
  createdAt: string;
  citations: Citation[];
  activeCitationOrder: number | null;
  onCitationClick: (order: number, citations: Citation[]) => void;
}) {
  const byOrder = citationsByOrder(citations);
  const segments = splitAnswerWithCitations(content);

  return (
    <div className="max-w-3xl rounded-md border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900">
      <p className="text-xs font-medium tracking-wide text-slate-500 uppercase">Assistant</p>
      <div className="mt-2 max-h-[min(32rem,60vh)] overflow-y-auto whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
        {segments.map((segment, index) => {
          if (segment.kind === "text") {
            return <span key={`t-${index}`}>{segment.value}</span>;
          }
          const citation = byOrder.get(segment.order);
          const available = citation !== undefined;
          return (
            <button
              key={`c-${index}-${segment.order}`}
              type="button"
              className={`mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded px-1 align-super text-xs font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900/40 ${
                available
                  ? activeCitationOrder === segment.order
                    ? "bg-slate-900 text-white"
                    : "bg-slate-200 text-slate-900 hover:bg-slate-300"
                  : "bg-slate-100 text-slate-400"
              }`}
              disabled={!available}
              aria-label={
                available
                  ? `Show citation ${segment.order}`
                  : `Citation ${segment.order} not available`
              }
              onClick={() => {
                if (available) {
                  onCitationClick(segment.order, citations);
                }
              }}
            >
              {segment.order}
            </button>
          );
        })}
      </div>
      {citations.length > 0 ? (
        <p className="mt-3 text-xs text-slate-500">
          {citations.length} citation{citations.length === 1 ? "" : "s"} — open in the Citations
          panel.
        </p>
      ) : null}
      <p className="mt-2 text-xs text-slate-500">{formatTimestamp(createdAt)}</p>
    </div>
  );
}
