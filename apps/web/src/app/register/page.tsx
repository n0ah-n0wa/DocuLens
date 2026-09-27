import type { Metadata } from "next";

import { RegisterForm } from "@/components/auth/register-form";
import { RedirectIfAuthenticated } from "@/lib/auth/guards";

export const metadata: Metadata = {
  title: "Create account · DocuLens",
};

export default function RegisterPage() {
  return (
    <RedirectIfAuthenticated>
      <RegisterForm />
    </RedirectIfAuthenticated>
  );
}
