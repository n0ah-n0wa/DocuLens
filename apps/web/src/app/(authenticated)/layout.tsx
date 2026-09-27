import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { RequireAuth } from "@/lib/auth/guards";

export default function AuthenticatedLayout({ children }: { children: ReactNode }) {
  return (
    <RequireAuth>
      <AppShell>{children}</AppShell>
    </RequireAuth>
  );
}
