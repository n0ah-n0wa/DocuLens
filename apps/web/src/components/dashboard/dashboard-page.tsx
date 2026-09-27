"use client";

import Link from "next/link";

import { DocumentListTable } from "@/components/documents/document-list-table";
import { ProcessingStatusBadge } from "@/components/documents/processing-status-badge";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/ui/query-state";
import { messageForApiError } from "@/lib/api/errors";
import { shouldPollProcessingStatus } from "@/lib/documents/status";
import { formatTimestamp } from "@/lib/format";
import { useCollectionsQuery } from "@/lib/hooks/use-collections";
import { useDocumentsQuery } from "@/lib/hooks/use-documents";

export function DashboardPageClient() {
  const documentsQuery = useDocumentsQuery();
  const collectionsQuery = useCollectionsQuery();

  const documentsLoading = documentsQuery.isPending && documentsQuery.data === undefined;
  const collectionsLoading = collectionsQuery.isPending && collectionsQuery.data === undefined;
  const documents = documentsQuery.data ?? [];
  const collections = collectionsQuery.data ?? [];
  const processing = documents.filter((doc) => shouldPollProcessingStatus(doc.processing_status));
  const readyCount = documents.filter((doc) => doc.processing_status === "READY").length;
  const failedCount = documents.filter((doc) => doc.processing_status === "FAILED").length;
  const recent = [...documents]
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 8);

  const documentsFailed = documentsQuery.isError && documentsQuery.data === undefined;
  const collectionsFailed = collectionsQuery.isError;

  return (
    <div className="space-y-8">
      <PageHeader
        title="Dashboard"
        description="Upload PDFs, organise collections, and track processing until documents are ready."
        actions={
          <>
            <Link
              href="/documents/upload"
              className="inline-flex items-center justify-center rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
            >
              Upload PDF
            </Link>
            <Link
              href="/collections"
              className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-900 hover:bg-slate-50"
            >
              Manage collections
            </Link>
          </>
        }
      />

      {documentsLoading && collectionsLoading ? (
        <LoadingState label="Loading dashboard" />
      ) : documentsFailed ? (
        <ErrorState
          title="Dashboard unavailable"
          message={messageForApiError(documentsQuery.error, "Could not load your documents.")}
          action={
            <Button
              variant="secondary"
              onClick={() => {
                void documentsQuery.refetch();
                void collectionsQuery.refetch();
              }}
            >
              Try again
            </Button>
          }
        />
      ) : (
        <>
          {collectionsFailed ? (
            <Alert tone="info" title="Collections unavailable">
              {messageForApiError(collectionsQuery.error)} Document data below is still available.
            </Alert>
          ) : null}

          <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Documents" value={String(documents.length)} />
            <StatCard label="Ready" value={String(readyCount)} />
            <StatCard label="Processing" value={String(processing.length)} />
            <StatCard
              label="Collections"
              value={collectionsFailed ? "—" : String(collections.length)}
            />
          </dl>

          {failedCount > 0 ? (
            <p className="text-sm text-red-800" role="status">
              {failedCount} document{failedCount === 1 ? "" : "s"} failed processing. Open a
              document to inspect the error and reprocess.
            </p>
          ) : null}

          {processing.length > 0 ? (
            <section
              className="space-y-3 rounded-md border border-amber-200 bg-amber-50/60 p-4"
              aria-labelledby="processing-heading"
            >
              <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                <h2 id="processing-heading" className="text-sm font-semibold text-amber-950">
                  In progress
                </h2>
                <p className="text-xs text-amber-900" role="status" aria-live="polite">
                  Status refreshes automatically.
                </p>
              </div>
              <ul className="space-y-2">
                {processing.slice(0, 6).map((doc) => (
                  <li
                    key={doc.id}
                    className="flex flex-wrap items-center justify-between gap-2 text-sm"
                  >
                    <Link
                      href={`/documents/${doc.id}`}
                      className="font-medium break-all text-slate-900 underline-offset-2 hover:underline"
                    >
                      {doc.filename}
                    </Link>
                    <ProcessingStatusBadge status={doc.processing_status} />
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <section className="space-y-3" aria-labelledby="recent-documents-heading">
            <div className="flex items-center justify-between gap-3">
              <h2 id="recent-documents-heading" className="text-sm font-semibold text-slate-900">
                Recent documents
              </h2>
              <Link
                href="/documents"
                className="text-sm font-medium text-slate-900 underline-offset-2 hover:underline"
              >
                View all
              </Link>
            </div>
            {documentsLoading ? (
              <LoadingState label="Loading documents" />
            ) : recent.length === 0 ? (
              <EmptyState
                title="No documents yet"
                message="Upload a PDF to start the processing pipeline."
                action={
                  <Link
                    href="/documents/upload"
                    className="inline-flex items-center justify-center rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
                  >
                    Upload PDF
                  </Link>
                }
              />
            ) : (
              <DocumentListTable documents={recent} collections={collections} />
            )}
          </section>

          <section className="space-y-3" aria-labelledby="collections-preview-heading">
            <div className="flex items-center justify-between gap-3">
              <h2 id="collections-preview-heading" className="text-sm font-semibold text-slate-900">
                Collections
              </h2>
              <Link
                href="/collections"
                className="text-sm font-medium text-slate-900 underline-offset-2 hover:underline"
              >
                Manage
              </Link>
            </div>
            {collectionsLoading ? (
              <LoadingState label="Loading collections" />
            ) : collectionsFailed ? (
              <ErrorState
                title="Could not load collections"
                message={messageForApiError(collectionsQuery.error)}
                action={
                  <Button variant="secondary" onClick={() => void collectionsQuery.refetch()}>
                    Try again
                  </Button>
                }
              />
            ) : collections.length === 0 ? (
              <EmptyState
                title="No collections yet"
                message="Create a collection to group related documents."
                action={
                  <Link
                    href="/collections"
                    className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-900 hover:bg-slate-50"
                  >
                    Create collection
                  </Link>
                }
              />
            ) : (
              <ul className="grid gap-3 sm:grid-cols-2">
                {collections.slice(0, 6).map((collection) => (
                  <li
                    key={collection.id}
                    className="rounded-md border border-slate-200 bg-white px-4 py-3"
                  >
                    <Link
                      href={`/collections/${collection.id}`}
                      className="font-medium break-words text-slate-900 underline-offset-2 hover:underline"
                    >
                      {collection.name}
                    </Link>
                    <p className="mt-1 text-xs text-slate-500">
                      Updated {formatTimestamp(collection.updated_at)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white px-4 py-3">
      <dt className="text-xs font-medium tracking-wide text-slate-500 uppercase">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">{value}</dd>
    </div>
  );
}
