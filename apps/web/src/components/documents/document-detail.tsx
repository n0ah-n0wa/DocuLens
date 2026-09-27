"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useRef, useState, type FormEvent } from "react";
import type { Document } from "@doculens/shared-types";

import { ProcessingStatusBadge } from "@/components/documents/processing-status-badge";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui/query-state";
import { Select } from "@/components/ui/select";
import { describeApiError, messageForApiError } from "@/lib/api/errors";
import { canReindex, canReprocess, shouldPollProcessingStatus } from "@/lib/documents/status";
import { formatBytes, formatTimestamp } from "@/lib/format";
import { useCollectionsQuery } from "@/lib/hooks/use-collections";
import {
  useDeleteDocumentMutation,
  useDocumentQuery,
  useReindexDocumentMutation,
  useReprocessDocumentMutation,
  useUpdateDocumentMutation,
} from "@/lib/hooks/use-documents";
import { renameDocumentSchema } from "@/lib/validation/documents";

type PendingAction = "reprocess" | "reindex" | "delete" | null;

export function DocumentDetailPage({ documentId }: { documentId: string }) {
  const documentQuery = useDocumentQuery(documentId);
  const collectionsQuery = useCollectionsQuery();

  if (documentQuery.isPending && documentQuery.data === undefined) {
    return <LoadingState label="Loading document" />;
  }

  if (documentQuery.isError && documentQuery.data === undefined) {
    return (
      <div className="space-y-4">
        <PageHeader title="Document" />
        <ErrorState
          title="Document unavailable"
          message={messageForApiError(documentQuery.error)}
          action={
            <Link
              href="/documents"
              className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-900 hover:bg-slate-50"
            >
              Back to documents
            </Link>
          }
        />
      </div>
    );
  }

  const document = documentQuery.data;
  if (!document) {
    return null;
  }

  return (
    <DocumentDetailView
      key={document.id}
      document={document}
      isRefreshing={documentQuery.isFetching && !documentQuery.isPending}
      collections={collectionsQuery.data ?? []}
      collectionsError={
        collectionsQuery.isError ? messageForApiError(collectionsQuery.error) : null
      }
      collectionsLoading={collectionsQuery.isPending && collectionsQuery.data === undefined}
    />
  );
}

function DocumentDetailView({
  document,
  isRefreshing,
  collections,
  collectionsError,
  collectionsLoading,
}: {
  document: Document;
  isRefreshing: boolean;
  collections: { id: string; name: string }[];
  collectionsError: string | null;
  collectionsLoading: boolean;
}) {
  const router = useRouter();
  const alertRef = useRef<HTMLDivElement>(null);
  const updateMutation = useUpdateDocumentMutation(document.id);
  const deleteMutation = useDeleteDocumentMutation();
  const reprocessMutation = useReprocessDocumentMutation(document.id);
  const reindexMutation = useReindexDocumentMutation(document.id);

  const [filename, setFilename] = useState(document.filename);
  const [collectionId, setCollectionId] = useState(document.collection_id ?? "");
  const [filenameError, setFilenameError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionErrorRequestId, setActionErrorRequestId] = useState<string | undefined>();
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);

  const polling = shouldPollProcessingStatus(document.processing_status);
  const busy =
    updateMutation.isPending ||
    deleteMutation.isPending ||
    reprocessMutation.isPending ||
    reindexMutation.isPending;

  const showActionError = useCallback((error: unknown, fallback: string) => {
    const described = describeApiError(error, fallback);
    setActionError(described.message);
    setActionErrorRequestId(described.requestId);
    queueMicrotask(() => alertRef.current?.focus());
  }, []);

  async function onSaveMeta(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) {
      return;
    }
    setActionError(null);
    setActionErrorRequestId(undefined);
    setActionNotice(null);
    setFilenameError(null);

    const parsed = renameDocumentSchema.safeParse({ filename });
    if (!parsed.success) {
      setFilenameError(parsed.error.issues[0]?.message ?? "Invalid filename.");
      return;
    }

    const body: {
      filename?: string;
      collection_id?: string | null;
    } = {};
    if (parsed.data.filename !== document.filename) {
      body.filename = parsed.data.filename;
    }
    const nextCollection = collectionId.length > 0 ? collectionId : null;
    if (nextCollection !== document.collection_id) {
      body.collection_id = nextCollection;
    }
    if (body.filename === undefined && body.collection_id === undefined) {
      setActionNotice("No changes to save.");
      return;
    }

    try {
      await updateMutation.mutateAsync(body);
      setActionNotice("Document updated.");
    } catch (error) {
      showActionError(error, "Could not update the document.");
    }
  }

  async function confirmPendingAction() {
    const action = pendingAction;
    if (!action || busy) {
      return;
    }
    setActionError(null);
    setActionErrorRequestId(undefined);
    setActionNotice(null);

    try {
      if (action === "reprocess") {
        await reprocessMutation.mutateAsync();
        setActionNotice("Reprocess started. Status will update automatically.");
      } else if (action === "reindex") {
        await reindexMutation.mutateAsync();
        setActionNotice("Reindex started. Status will update automatically.");
      } else if (action === "delete") {
        await deleteMutation.mutateAsync(document.id);
        setPendingAction(null);
        router.replace("/documents");
        return;
      }
      setPendingAction(null);
    } catch (error) {
      setPendingAction(null);
      showActionError(error, "Action failed.");
    }
  }

  const confirmCopy =
    pendingAction === "delete"
      ? {
          title: "Delete document?",
          description: (
            <>
              Delete “{document.filename}”? Stored content and derived data will be removed. This
              cannot be undone from the UI.
            </>
          ),
          confirmLabel: "Delete document",
          tone: "danger" as const,
        }
      : pendingAction === "reprocess"
        ? {
            title: "Reprocess document?",
            description:
              "Reprocess restarts the pipeline from the stored original PDF. Existing chunks and vectors will be replaced when processing completes.",
            confirmLabel: "Start reprocess",
            tone: "default" as const,
          }
        : pendingAction === "reindex"
          ? {
              title: "Reindex document?",
              description:
                "Reindex re-chunks and re-embeds from extracted pages. Use this when pages already exist and you do not need to re-extract the PDF.",
              confirmLabel: "Start reindex",
              tone: "default" as const,
            }
          : null;

  return (
    <div className="space-y-6">
      <PageHeader
        title={document.filename}
        description="Inspect metadata, follow processing progress, and run lifecycle actions."
        actions={
          <Link
            href="/documents"
            className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-900 hover:bg-slate-50"
          >
            All documents
          </Link>
        }
      />

      {actionError ? (
        <div ref={alertRef} tabIndex={-1} className="outline-none">
          <Alert title="Action failed" requestId={actionErrorRequestId}>
            {actionError}
          </Alert>
        </div>
      ) : null}
      {actionNotice ? <Alert tone="info">{actionNotice}</Alert> : null}
      {collectionsError ? (
        <Alert tone="info" title="Collections unavailable">
          {collectionsError} You can still view and process this document; moving it between
          collections is temporarily unavailable.
        </Alert>
      ) : null}

      <section
        className="rounded-md border border-slate-200 bg-white p-5"
        aria-labelledby="document-status-heading"
      >
        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 id="document-status-heading" className="text-sm font-semibold text-slate-900">
              Processing status
            </h2>
            {isRefreshing || polling ? (
              <p className="text-xs text-slate-500" role="status" aria-live="polite">
                {polling ? "Updating automatically while processing continues." : "Refreshing…"}
              </p>
            ) : null}
          </div>
          <ProcessingStatusBadge status={document.processing_status} />
          {document.processing_error ? (
            <Alert title="Processing error">{document.processing_error}</Alert>
          ) : null}
        </div>
        <dl className="mt-4 grid gap-4 sm:grid-cols-2">
          <MetaItem label="Size" value={formatBytes(document.file_size)} />
          <MetaItem
            label="Pages"
            value={document.page_count === null ? "—" : String(document.page_count)}
          />
          <MetaItem label="Chunks" value={String(document.chunk_count)} />
          <MetaItem label="MIME type" value={document.mime_type} />
          <MetaItem label="Created" value={formatTimestamp(document.created_at)} />
          <MetaItem label="Updated" value={formatTimestamp(document.updated_at)} />
          <MetaItem
            label="Indexed"
            value={document.indexed_at ? formatTimestamp(document.indexed_at) : "—"}
          />
          <MetaItem label="Document ID" value={document.id} />
        </dl>
      </section>

      <section
        className="space-y-4 rounded-md border border-slate-200 bg-white p-5"
        aria-labelledby="document-edit-heading"
      >
        <h2 id="document-edit-heading" className="text-sm font-semibold text-slate-900">
          Rename or move
        </h2>
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={(event) => void onSaveMeta(event)}>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="document-filename">Filename</Label>
            <Input
              id="document-filename"
              value={filename}
              onChange={(event) => setFilename(event.target.value)}
              invalid={filenameError !== null}
              aria-describedby={filenameError ? "document-filename-error" : undefined}
              maxLength={255}
              required
              disabled={busy}
            />
            {filenameError ? (
              <p id="document-filename-error" className="text-xs text-red-700" role="alert">
                {filenameError}
              </p>
            ) : null}
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="document-collection">Collection</Label>
            <Select
              id="document-collection"
              value={collectionId}
              onChange={(event) => setCollectionId(event.target.value)}
              disabled={busy || collectionsLoading || collectionsError !== null}
            >
              <option value="">No collection</option>
              {collections.map((collection) => (
                <option key={collection.id} value={collection.id}>
                  {collection.name}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Button type="submit" loading={updateMutation.isPending} disabled={busy}>
              Save changes
            </Button>
          </div>
        </form>
      </section>

      <section
        className="space-y-3 rounded-md border border-slate-200 bg-white p-5"
        aria-labelledby="document-actions-heading"
      >
        <h2 id="document-actions-heading" className="text-sm font-semibold text-slate-900">
          Actions
        </h2>
        <p className="text-sm text-slate-600">
          Reprocess restarts from the stored original. Reindex re-chunks and re-embeds from
          extracted pages when pages already exist.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            disabled={busy || !canReprocess(document.processing_status)}
            onClick={() => setPendingAction("reprocess")}
          >
            Reprocess
          </Button>
          <Button
            variant="secondary"
            disabled={busy || !canReindex(document.processing_status)}
            onClick={() => setPendingAction("reindex")}
          >
            Reindex
          </Button>
          <Button
            variant="danger"
            disabled={busy || document.processing_status === "DELETING"}
            onClick={() => setPendingAction("delete")}
          >
            Delete
          </Button>
        </div>
      </section>

      {confirmCopy ? (
        <ConfirmDialog
          open={pendingAction !== null}
          title={confirmCopy.title}
          description={confirmCopy.description}
          confirmLabel={confirmCopy.confirmLabel}
          tone={confirmCopy.tone}
          busy={
            (pendingAction === "delete" && deleteMutation.isPending) ||
            (pendingAction === "reprocess" && reprocessMutation.isPending) ||
            (pendingAction === "reindex" && reindexMutation.isPending)
          }
          onCancel={() => {
            if (!busy) {
              setPendingAction(null);
            }
          }}
          onConfirm={() => {
            void confirmPendingAction();
          }}
        />
      ) : null}
    </div>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-medium tracking-wide text-slate-500 uppercase">{label}</dt>
      <dd className="mt-1 break-all text-sm text-slate-900">{value}</dd>
    </div>
  );
}
