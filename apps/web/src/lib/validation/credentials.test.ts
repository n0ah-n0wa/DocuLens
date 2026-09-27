import { describe, expect, it } from "vitest";

import { credentialsSchema, registerCredentialsSchema } from "@/lib/validation/credentials";

describe("credentialsSchema", () => {
  it("accepts a valid login payload", () => {
    const result = credentialsSchema.safeParse({
      email: "user@example.com",
      password: "short",
    });
    expect(result.success).toBe(true);
  });

  it("rejects an empty email", () => {
    const result = credentialsSchema.safeParse({ email: " ", password: "password" });
    expect(result.success).toBe(false);
  });
});

describe("registerCredentialsSchema", () => {
  it("requires at least 12 password characters", () => {
    const short = registerCredentialsSchema.safeParse({
      email: "user@example.com",
      password: "tooshort1",
    });
    expect(short.success).toBe(false);

    const ok = registerCredentialsSchema.safeParse({
      email: "user@example.com",
      password: "long-enough-password",
    });
    expect(ok.success).toBe(true);
  });
});
