import { describe, expect, it } from "vitest";
import { ZodError } from "zod";

import { ApiError } from "@/lib/api/errors";
import { fieldErrorsFromApiError, fieldErrorsFromZod } from "@/lib/validation/form-errors";

describe("fieldErrorsFromZod", () => {
  it("keeps the first message per field", () => {
    const error = new ZodError([
      {
        code: "custom",
        path: ["email"],
        message: "Enter your email address.",
      },
      {
        code: "custom",
        path: ["email"],
        message: "Enter a valid email address.",
      },
    ]);
    expect(fieldErrorsFromZod(error)).toEqual({ email: "Enter your email address." });
  });
});

describe("fieldErrorsFromApiError", () => {
  it("maps body.email details onto the email field", () => {
    const error = new ApiError(422, {
      code: "VALIDATION_ERROR",
      message: "Request validation failed.",
      request_id: "req-1",
      details: [
        {
          location: "body.email",
          message: "Enter a valid email address.",
          type: "value_error",
        },
      ],
    });
    expect(fieldErrorsFromApiError(error)).toEqual({
      email: "Enter a valid email address.",
    });
  });
});
