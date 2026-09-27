import type { Metadata } from "next";

import { DashboardPageClient } from "@/components/dashboard/dashboard-page";

export const metadata: Metadata = {
  title: "Dashboard · DocuLens",
};

export default function DashboardPage() {
  return <DashboardPageClient />;
}
