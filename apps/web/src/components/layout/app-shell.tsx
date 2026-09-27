"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth/auth-context";

const navItems = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/documents", label: "Documents" },
  { href: "/collections", label: "Collections" },
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [signingOut, setSigningOut] = useState(false);

  async function onSignOut() {
    setSigningOut(true);
    try {
      await logout();
      router.replace("/login");
    } finally {
      setSigningOut(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
          <div className="flex min-w-0 items-center gap-6">
            <Link href="/dashboard" className="text-sm font-semibold tracking-wide uppercase">
              DocuLens
            </Link>
            <nav
              aria-label="Primary"
              className="hidden items-center gap-4 text-sm text-slate-600 sm:flex"
            >
              {navItems.map((item) => {
                const active =
                  pathname === item.href ||
                  (item.href !== "/dashboard" && pathname.startsWith(item.href));
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={active ? "font-medium text-slate-900" : "hover:text-slate-900"}
                    aria-current={active ? "page" : undefined}
                  >
                    {item.label}
                  </Link>
                );
              })}
            </nav>
          </div>
          <div className="flex items-center gap-3">
            {user?.email ? (
              <p className="hidden text-sm text-slate-600 md:block" data-testid="signed-in-email">
                {user.email}
              </p>
            ) : null}
            <Button variant="secondary" loading={signingOut} onClick={() => void onSignOut()}>
              {signingOut ? "Signing out…" : "Sign out"}
            </Button>
          </div>
        </div>
        <nav
          aria-label="Primary mobile"
          className="flex gap-4 overflow-x-auto border-t border-slate-100 px-4 py-2 text-sm text-slate-600 sm:hidden"
        >
          {navItems.map((item) => {
            const active =
              pathname === item.href ||
              (item.href !== "/dashboard" && pathname.startsWith(item.href));
            return (
              <Link
                key={item.href}
                href={item.href}
                className={
                  active ? "font-medium whitespace-nowrap text-slate-900" : "whitespace-nowrap"
                }
                aria-current={active ? "page" : undefined}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </header>
      <div className="mx-auto max-w-6xl px-4 py-8">{children}</div>
    </div>
  );
}
