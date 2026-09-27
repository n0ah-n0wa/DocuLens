"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { DocumentListTable } from "@/components/documents/document-list-table";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/ui/query-state";
import { Select } from "@/components/ui/select";
import { messageForApiError } from "@/lib/api/errors";
import { useCollectionsQuery } from "@/lib/hooks/use-collections";
import { useDocumentsQuery } from "@/lib/hooks/use-documents";

export function DocumentsPageClient({ initialCollectionId }: { initialCollectionId?: string }) {
  const [collectionDraft, setCollectionDraft] = useState(initialCollectionId ?? "");
  const [searchDraft, setSearchDraft] = useState("");
  const [appliedCollectionId, setAppliedCollectionId] = useState(initialCollectionId ?? "");
  const [appliedSearch, setAppliedSearch] = useState("");

  const listParams = useMemo(
    () => ({
      ...(appliedCollectionId ? { collection_id: appliedCollectionId } : {}),
      ...(appliedSearch ? { q: appliedSearch } : {}),
    }),
    [appliedCollectionId, appliedSearch],
  );

  const documentsQuery = useDocumentsQuery(listParams);
  const collectionsQuery = useCollectionsQuery();

  const collections = collectionsQuery.data ?? [];
  const documents = documentsQuery.data ?? [];
  const filtersActive = appliedSearch.length > 0 || appliedCollectionId.length > 0;
  const showInitialLoading = documentsQuery.isPending && documentsQuery.data === undefined;

  function applyFilters() {
    setAppliedSearch(searchDraft.trim());
    setAppliedCollectionId(collectionDraft);
  }

  function clearFilters() {
    setSearchDraft("");
    setCollectionDraft("");
    setAppliedSearch("");
    setAppliedCollectionId("");
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Documents"
        description="Search and filter your PDFs. Processing status updates automatically while work is in progress."
        actions={
          <Link
            href="/documents/upload"
            className="inline-flex items-center justify-center rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
          >
            Upload PDF
          </Link>
        }
      />

      <form
        className="grid gap-3 rounded-md border border-slate-200 bg-white p-4 sm:grid-cols-[1fr_12rem_auto]"
        onSubmit={(event) => {
          event.preventDefault();
          applyFilters();
        }}
      >
        <div className="space-y-1.5">
          <Label htmlFor="document-search">Filename search</Label>
          <Input
            id="document-search"
            name="q"
            value={searchDraft}
            onChange={(event) => setSearchDraft(event.target.value)}
            placeholder="e.g. invoice"
            maxLength={255}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="document-collection-filter">Collection</Label>
          <Select
            id="document-collection-filter"
            value={collectionDraft}
            onChange={(event) => setCollectionDraft(event.target.value)}
            disabled={collectionsQuery.isPending || collectionsQuery.isError}
          >
            <option value="">All collections</option>
            {collections.map((collection) => (
              <option key={collection.id} value={collection.id}>
                {collection.name}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex items-end gap-2">
          <Button type="submit" variant="secondary" className="w-full sm:w-auto">
            Apply filters
          </Button>
        </div>
      </form>

      {collectionsQuery.isError ? (
        <Alert tone="info" title="Collections unavailable">
          {messageForApiError(collectionsQuery.error)} Filtering by collection is temporarily
          disabled.
        </Alert>
      ) : null}

      {showInitialLoading ? (
        <LoadingState label="Loading documents" />
      ) : documentsQuery.isError && documentsQuery.data === undefined ? (
        <ErrorState
          title="Could not load documents"
          message={messageForApiError(documentsQuery.error)}
          action={
            <Button variant="secondary" onClick={() => void documentsQuery.refetch()}>
              Try again
            </Button>
          }
        />
      ) : documents.length === 0 && !filtersActive ? (
        <EmptyState
          title="No documents yet"
          message="Upload a PDF to start asynchronous processing."
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
        <div className="space-y-2">
          {documentsQuery.isFetching && !documentsQuery.isPending ? (
            <p className="text-xs text-slate-500" role="status" aria-live="polite">
              Refreshing…
            </p>
          ) : null}
          <DocumentListTable
            documents={documents}
            collections={collections}
            emptyTitle="No matching documents"
            emptyMessage="Try a different filename search or collection filter."
            emptyAction={
              <Button variant="secondary" onClick={clearFilters}>
                Clear filters
              </Button>
            }
          />
        </div>
      )}
    </div>
  );
}
