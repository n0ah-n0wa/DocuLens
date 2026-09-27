"use client";

import Link from "next/link";
import type { Citation, Document } from "@doculens/shared-types";

/**
 * First-class citation card. Filename comes only from the documents API —
 * never invented when the document is unknown.
 *
 * Selection is a dedicated control so document links are not nested inside another
 * interactive element.
 */
export function CitationCard({
  citation,
  document,
  active = false,
  id,
  onActivate,
}: {
  citation: Citation;
  document: Document | undefined;
  active?: boolean;
  id?: string;
  onActivate?: () => void;
}) {
  const filename = document?.filename;
  const knownDocument = document !== undefined;

  return (
    <article
      id={id}
      className={`rounded-md border px-3 py-3 text-sm scroll-mt-4 ${
        active
          ? "border-slate-900 bg-slate-50 ring-2 ring-slate-900/20"
          : "border-slate-200 bg-white"
      }`}
      aria-current={active ? "true" : undefined}
    >
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="min-w-0 font-medium text-slate-900">
          <span className="mr-2 inline-flex h-5 min-w-5 items-center justify-center rounded bg-slate-900 px-1 text-xs text-white">
            {citation.citation_order}
          </span>
          {knownDocument && filename ? (
            <Link
              href={`/documents/${citation.document_id}`}
              className="break-all underline-offset-2 hover:underline"
            >
              {filename}
            </Link>
          ) : (
            <span className="text-slate-700">Source document unavailable</span>
          )}
        </p>
        <p className="shrink-0 text-xs text-slate-500">Page {citation.page_number}</p>
      </header>

      <blockquote className="mt-2 max-h-40 overflow-y-auto border-l-2 border-slate-300 pl-3 text-slate-700 break-words whitespace-pre-wrap [overflow-wrap:anywhere]">
        {citation.quoted_text}
      </blockquote>

      <dl className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-500">
        <div>
          <dt className="font-medium tracking-wide uppercase">Retrieval</dt>
          <dd>{citation.retrieval_score.toFixed(3)}</dd>
        </div>
        <div>
          <dt className="font-medium tracking-wide uppercase">Rerank</dt>
          <dd>{citation.reranking_score === null ? "—" : citation.reranking_score.toFixed(3)}</dd>
        </div>
      </dl>

      <p className="mt-2 text-xs text-slate-500">
        Citations show retrieved evidence, not a guarantee of answer correctness.
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        {onActivate ? (
          <button
            type="button"
            className="text-xs font-medium text-slate-900 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900/30"
            onClick={onActivate}
            aria-pressed={active}
          >
            {active ? "Selected source" : "Select source"}
          </button>
        ) : null}
        {knownDocument ? (
          <Link
            href={`/documents/${citation.document_id}`}
            className="text-xs font-medium text-slate-900 underline-offset-2 hover:underline"
          >
            Open document · cited page {citation.page_number}
          </Link>
        ) : (
          <span className="font-mono text-xs break-all text-slate-500">
            document_id: {citation.document_id}
          </span>
        )}
      </div>
    </article>
  );
}

export function CitationPanel({
  citations,
  documentsById,
  activeOrder,
  onSelect,
}: {
  citations: Citation[];
  documentsById: Map<string, Document>;
  activeOrder: number | null;
  onSelect: (order: number) => void;
}) {
  const ordered = [...citations].sort((a, b) => a.citation_order - b.citation_order);

  if (ordered.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-slate-200 bg-white px-4 py-6 text-sm text-slate-600">
        No citations for this answer.
      </div>
    );
  }

  return (
    <div
      className="max-h-[min(28rem,50vh)] space-y-3 overflow-y-auto overscroll-contain pr-1 xl:max-h-[min(36rem,70vh)]"
      role="list"
      aria-label="Citations"
    >
      {ordered.map((citation) => (
        <div key={citation.id} role="listitem">
          <CitationCard
            id={`citation-${citation.citation_order}`}
            citation={citation}
            document={documentsById.get(citation.document_id)}
            active={activeOrder === citation.citation_order}
            onActivate={() => onSelect(citation.citation_order)}
          />
        </div>
      ))}
    </div>
  );
}
