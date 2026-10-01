import { CollectionDetailPage } from "@/components/collections/collection-detail";

/** Placeholder path so `output: "export"` emits at least one HTML shell. */
export function generateStaticParams(): { collectionId: string }[] {
  return [{ collectionId: "_" }];
}

export default async function CollectionPage({
  params,
}: {
  params: Promise<{ collectionId: string }>;
}) {
  const { collectionId } = await params;
  return <CollectionDetailPage collectionId={collectionId} />;
}
