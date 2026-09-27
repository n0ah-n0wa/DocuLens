import type { Metadata } from "next";

import { DocumentDetailPage } from "@/components/documents/document-detail";

export const metadata: Metadata = {
  title: "Document · DocuLens",
};

export default async function DocumentPage({
  params,
}: {
  params: Promise<{ documentId: string }>;
}) {
  const { documentId } = await params;
  return <DocumentDetailPage documentId={documentId} />;
}
