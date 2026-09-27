import type { Metadata } from "next";

import { CollectionDetailPage } from "@/components/collections/collection-detail";

export const metadata: Metadata = {
  title: "Collection · DocuLens",
};

export default async function CollectionPage({
  params,
}: {
  params: Promise<{ collectionId: string }>;
}) {
  const { collectionId } = await params;
  return <CollectionDetailPage collectionId={collectionId} />;
}
