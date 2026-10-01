"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { DocumentsPageClient } from "@/components/documents/documents-page";
import { LoadingState } from "@/components/ui/query-state";

function DocumentsPageInner() {
  const searchParams = useSearchParams();
  const collectionId = searchParams.get("collection_id");
  return (
    <DocumentsPageClient
      {...(collectionId !== null ? { initialCollectionId: collectionId } : {})}
    />
  );
}

export default function DocumentsPage() {
  return (
    <Suspense fallback={<LoadingState label="Loading documents" />}>
      <DocumentsPageInner />
    </Suspense>
  );
}
