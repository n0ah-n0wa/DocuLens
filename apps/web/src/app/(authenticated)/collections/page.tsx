import type { Metadata } from "next";

import { CollectionsPageClient } from "@/components/collections/collections-page";

export const metadata: Metadata = {
  title: "Collections · DocuLens",
};

export default function CollectionsPage() {
  return <CollectionsPageClient />;
}
