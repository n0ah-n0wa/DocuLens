import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Dashboard · DocuLens",
};

export default function DashboardPage() {
  return (
    <main className="space-y-3">
      <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
      <p className="max-w-2xl text-slate-600">
        Document collections and grounded chat will appear here. This placeholder confirms the
        authenticated application shell is wired up.
      </p>
      <p role="status" className="text-sm text-slate-500">
        No documents yet — upload and chat are not available in this build.
      </p>
    </main>
  );
}
