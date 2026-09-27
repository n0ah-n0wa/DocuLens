import type { Metadata } from "next";

import { LoginForm } from "@/components/auth/login-form";
import { RedirectIfAuthenticated } from "@/lib/auth/guards";

export const metadata: Metadata = {
  title: "Sign in · DocuLens",
};

export default function LoginPage() {
  return (
    <RedirectIfAuthenticated>
      <LoginForm />
    </RedirectIfAuthenticated>
  );
}
