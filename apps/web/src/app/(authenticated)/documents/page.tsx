import type { Metadata } from "next";

import { DocumentsPageClient } from "@/components/documents/documents-page";

export const metadata: Metadata = {
  title: "Documents · DocuLens",
};

export default async function DocumentsPage({
  searchParams,
}: {
  searchParams: Promise<{ collection_id?: string }>;
}) {
  const params = await searchParams;
  return (
    <DocumentsPageClient
      {...(params.collection_id !== undefined ? { initialCollectionId: params.collection_id } : {})}
    />
  );
}
