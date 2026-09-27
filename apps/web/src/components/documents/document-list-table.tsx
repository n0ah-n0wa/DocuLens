"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import type { Collection, Document } from "@doculens/shared-types";

import { ProcessingStatusBadge } from "@/components/documents/processing-status-badge";
import { EmptyState } from "@/components/ui/query-state";
import { formatBytes, formatTimestamp } from "@/lib/format";

export function DocumentListTable({
  documents,
  collections,
  emptyTitle = "No documents yet",
  emptyMessage = "Upload a PDF to start processing. Grounded chat will use ready documents.",
  emptyAction,
}: {
  documents: Document[];
  collections: Collection[];
  emptyTitle?: string;
  emptyMessage?: string;
  emptyAction?: ReactNode;
}) {
  const collectionName = new Map(collections.map((c) => [c.id, c.name]));

  if (documents.length === 0) {
    return <EmptyState title={emptyTitle} message={emptyMessage} action={emptyAction} />;
  }

  return (
    <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
      <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
        <caption className="sr-only">Documents</caption>
        <thead className="bg-slate-50 text-xs tracking-wide text-slate-500 uppercase">
          <tr>
            <th scope="col" className="px-4 py-3 font-medium">
              Filename
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              Status
            </th>
            <th scope="col" className="hidden px-4 py-3 font-medium md:table-cell">
              Collection
            </th>
            <th scope="col" className="hidden px-4 py-3 font-medium sm:table-cell">
              Size
            </th>
            <th scope="col" className="hidden px-4 py-3 font-medium lg:table-cell">
              Updated
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {documents.map((doc) => {
            const collectionLabel = doc.collection_id
              ? (collectionName.get(doc.collection_id) ?? "Unknown collection")
              : null;
            return (
              <tr key={doc.id} className="hover:bg-slate-50/80">
                <td className="px-4 py-3">
                  <Link
                    href={`/documents/${doc.id}`}
                    className="font-medium break-all text-slate-900 underline-offset-2 hover:underline"
                  >
                    {doc.filename}
                  </Link>
                  <p className="mt-1 text-xs text-slate-500 sm:hidden">
                    {formatBytes(doc.file_size)}
                    {collectionLabel ? ` · ${collectionLabel}` : ""}
                  </p>
                  {doc.processing_status === "FAILED" && doc.processing_error ? (
                    <p className="mt-1 max-w-md text-xs text-red-700 sm:truncate">
                      {doc.processing_error}
                    </p>
                  ) : null}
                </td>
                <td className="px-4 py-3">
                  <ProcessingStatusBadge status={doc.processing_status} />
                </td>
                <td className="hidden px-4 py-3 text-slate-600 md:table-cell">
                  {collectionLabel ?? "—"}
                </td>
                <td className="hidden px-4 py-3 text-slate-600 sm:table-cell">
                  {formatBytes(doc.file_size)}
                </td>
                <td className="hidden px-4 py-3 text-slate-600 lg:table-cell">
                  {formatTimestamp(doc.updated_at)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
