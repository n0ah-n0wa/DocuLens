import { describe, expect, it } from "vitest";

import { ApiError, describeApiError, messageForApiError } from "@/lib/api/errors";

describe("describeApiError", () => {
  it("enriches duplicate uploads with the existing document id", () => {
    const error = new ApiError(409, {
      code: "DUPLICATE_DOCUMENT",
      message: "This file was already uploaded.",
      request_id: "req-dup",
      details: [
        {
          location: "existing_document_id",
          message: "11111111-1111-1111-1111-111111111111",
          type: "conflict",
        },
      ],
    });

    expect(describeApiError(error)).toEqual({
      message: "This file was already uploaded. Open the existing document to continue.",
      code: "DUPLICATE_DOCUMENT",
      requestId: "req-dup",
      existingDocumentId: "11111111-1111-1111-1111-111111111111",
    });
  });

  it("surfaces rate-limit failures clearly", () => {
    const error = new ApiError(429, {
      code: "RATE_LIMITED",
      message: "",
      request_id: "req-rl",
    });
    expect(describeApiError(error).message).toContain("Too many requests");
  });

  it("keeps messageForApiError compatible", () => {
    const error = new ApiError(400, {
      code: "EMPTY_FILE",
      message: "The file is empty.",
      request_id: "req-1",
    });
    expect(messageForApiError(error)).toBe("The file is empty.");
  });
});
