import { DocumentDetailPage } from "@/components/documents/document-detail";

/** Placeholder path so `output: "export"` emits at least one HTML shell. */
export function generateStaticParams(): { documentId: string }[] {
  return [{ documentId: "_" }];
}

export default async function DocumentPage({
  params,
}: {
  params: Promise<{ documentId: string }>;
}) {
  const { documentId } = await params;
  return <DocumentDetailPage documentId={documentId} />;
}
