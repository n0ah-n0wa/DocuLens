import type { Metadata } from "next";

import { UploadDocumentForm } from "@/components/documents/upload-document-form";

export const metadata: Metadata = {
  title: "Upload · DocuLens",
};

export default async function UploadDocumentPage({
  searchParams,
}: {
  searchParams: Promise<{ collection_id?: string }>;
}) {
  const params = await searchParams;
  return <UploadDocumentForm initialCollectionId={params.collection_id ?? ""} />;
}
