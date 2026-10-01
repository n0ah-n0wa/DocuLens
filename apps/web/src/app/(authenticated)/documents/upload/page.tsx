"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { UploadDocumentForm } from "@/components/documents/upload-document-form";
import { LoadingState } from "@/components/ui/query-state";

function UploadDocumentPageInner() {
  const searchParams = useSearchParams();
  return <UploadDocumentForm initialCollectionId={searchParams.get("collection_id") ?? ""} />;
}

export default function UploadDocumentPage() {
  return (
    <Suspense fallback={<LoadingState label="Loading upload form" />}>
      <UploadDocumentPageInner />
    </Suspense>
  );
}
