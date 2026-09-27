import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/components/auth/login-form";

const login = vi.fn();
const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), prefetch: vi.fn() }),
}));

vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({
    status: "anonymous",
    user: null,
    login,
    register: vi.fn(),
    logout: vi.fn(),
    refreshProfile: vi.fn(),
  }),
}));

describe("LoginForm", () => {
  beforeEach(() => {
    login.mockReset();
    replace.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("shows field validation before calling login", async () => {
    const user = userEvent.setup();
    render(<LoginForm />);

    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(screen.getByText("Enter your email address.")).toBeInTheDocument();
    expect(login).not.toHaveBeenCalled();
  });

  it("submits valid credentials and navigates to the dashboard", async () => {
    login.mockResolvedValueOnce(undefined);
    const user = userEvent.setup();
    render(<LoginForm />);

    await user.type(screen.getByLabelText("Email"), "user@example.com");
    await user.type(screen.getByLabelText("Password"), "password-value");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(login).toHaveBeenCalledWith({
      email: "user@example.com",
      password: "password-value",
    });
    expect(replace).toHaveBeenCalledWith("/dashboard");
  });

  it("surfaces API errors from a failed login", async () => {
    login.mockRejectedValueOnce(new Error("The email address or password is incorrect."));
    const user = userEvent.setup();
    render(<LoginForm />);

    await user.type(screen.getByLabelText("Email"), "user@example.com");
    await user.type(screen.getByLabelText("Password"), "password-value");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(
      await screen.findByText("The email address or password is incorrect."),
    ).toBeInTheDocument();
  });
});
