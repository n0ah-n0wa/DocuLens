"use client";

import { useMemo, useRef, useState, type FormEvent } from "react";
import type { Collection, Document } from "@doculens/shared-types";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, Textarea } from "@/components/ui/select";
import { askQuestionSchema } from "@/lib/validation/chat";

export type ComposerScopeMode = "conversation" | "documents";

export function QuestionComposer({
  disabled = false,
  busy = false,
  error,
  errorRequestId,
  readyDocuments,
  collections,
  conversationCollectionId,
  onAsk,
  onCancel,
  canRetry = false,
  onRetry,
}: {
  disabled?: boolean;
  busy?: boolean;
  error?: string | null;
  errorRequestId?: string | undefined;
  readyDocuments: Document[];
  collections: Collection[];
  conversationCollectionId: string | null;
  onAsk: (input: { question: string; documentIds?: string[] }) => Promise<void>;
  onCancel?: () => void;
  canRetry?: boolean;
  onRetry?: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [questionError, setQuestionError] = useState<string | undefined>();
  const [scopeMode, setScopeMode] = useState<ComposerScopeMode>("conversation");
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const submittingRef = useRef(false);

  const collectionName = useMemo(() => {
    if (!conversationCollectionId) {
      return null;
    }
    return collections.find((c) => c.id === conversationCollectionId)?.name ?? null;
  }, [collections, conversationCollectionId]);

  const scopeSummary =
    scopeMode === "documents" && selectedDocumentIds.length > 0
      ? `${selectedDocumentIds.length} selected document${selectedDocumentIds.length === 1 ? "" : "s"}`
      : conversationCollectionId
        ? collectionName
          ? `Collection: ${collectionName}`
          : "Conversation collection"
        : "All READY documents";

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || disabled || submittingRef.current) {
      return;
    }
    setQuestionError(undefined);
    const parsed = askQuestionSchema.safeParse({ question });
    if (!parsed.success) {
      setQuestionError(parsed.error.issues[0]?.message ?? "Enter a question.");
      return;
    }
    if (scopeMode === "documents" && selectedDocumentIds.length === 0) {
      setQuestionError("Select at least one READY document, or use conversation scope.");
      return;
    }

    const payload =
      scopeMode === "documents"
        ? { question: parsed.data.question, documentIds: selectedDocumentIds }
        : { question: parsed.data.question };

    submittingRef.current = true;
    try {
      await onAsk(payload);
      setQuestion("");
    } finally {
      submittingRef.current = false;
    }
  }

  function toggleDocument(documentId: string) {
    setSelectedDocumentIds((current) =>
      current.includes(documentId)
        ? current.filter((id) => id !== documentId)
        : [...current, documentId],
    );
  }

  const describedBy = [questionError ? "chat-question-error" : null, "chat-scope-summary"]
    .filter(Boolean)
    .join(" ");

  return (
    <form
      className="space-y-3 rounded-md border border-slate-200 bg-white p-4"
      onSubmit={(event) => void onSubmit(event)}
      noValidate
    >
      {error ? (
        <Alert title="Could not send question" requestId={errorRequestId}>
          {error}
        </Alert>
      ) : null}

      <div className="space-y-1.5">
        <Label htmlFor="chat-question">Question</Label>
        <Textarea
          id="chat-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          rows={3}
          maxLength={100_000}
          disabled={busy || disabled}
          placeholder="Ask something that can be answered from your documents…"
          aria-invalid={questionError !== undefined ? true : undefined}
          aria-describedby={describedBy}
          required
        />
        {questionError ? (
          <p id="chat-question-error" className="text-xs text-red-700" role="alert">
            {questionError}
          </p>
        ) : null}
        <p id="chat-scope-summary" className="text-xs text-slate-500">
          Retrieval scope: {scopeSummary}
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="chat-scope-mode">Scope for this question</Label>
          <Select
            id="chat-scope-mode"
            value={scopeMode}
            disabled={busy || disabled}
            onChange={(event) => setScopeMode(event.target.value as ComposerScopeMode)}
          >
            <option value="conversation">
              {conversationCollectionId
                ? "Use conversation collection (or all docs if none)"
                : "Use all READY documents"}
            </option>
            <option value="documents">Select specific READY documents</option>
          </Select>
        </div>
      </div>

      {scopeMode === "documents" ? (
        <fieldset className="space-y-2 rounded-md border border-slate-100 bg-slate-50 p-3">
          <legend className="px-1 text-sm font-medium text-slate-800">READY documents</legend>
          {readyDocuments.length === 0 ? (
            <p className="text-sm text-slate-600">
              No READY documents yet. Finish processing uploads before asking.
            </p>
          ) : (
            <ul className="max-h-40 space-y-2 overflow-y-auto">
              {readyDocuments.map((document) => {
                const checked = selectedDocumentIds.includes(document.id);
                return (
                  <li key={document.id}>
                    <label className="flex items-start gap-2 text-sm text-slate-800">
                      <input
                        type="checkbox"
                        className="mt-1"
                        checked={checked}
                        disabled={busy || disabled}
                        onChange={() => toggleDocument(document.id)}
                      />
                      <span className="break-all">{document.filename}</span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </fieldset>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-slate-500">
          Answers and citation quotes are shown as text only. Citations are evidence, not a
          correctness guarantee.
        </p>
        <div className="flex flex-wrap gap-2">
          {busy && onCancel ? (
            <Button type="button" variant="secondary" onClick={onCancel}>
              Stop
            </Button>
          ) : null}
          {!busy && canRetry && onRetry ? (
            <Button type="button" variant="secondary" onClick={onRetry}>
              Retry
            </Button>
          ) : null}
          <Button type="submit" loading={busy} disabled={disabled || busy}>
            {busy ? "Asking…" : "Ask"}
          </Button>
        </div>
      </div>
    </form>
  );
}
