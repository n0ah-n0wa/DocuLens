import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "@/lib/api/client";
import { clearSessionTokens, setAccessToken } from "@/lib/auth/session";

describe("apiRequest multipart", () => {
  afterEach(() => {
    clearSessionTokens();
    vi.unstubAllGlobals();
  });

  it("sends FormData without forcing Content-Type", async () => {
    setAccessToken("access-token");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "doc-1",
          collection_id: null,
          filename: "a.pdf",
          mime_type: "application/pdf",
          file_size: 12,
          page_count: null,
          processing_status: "UPLOADED",
          processing_error: null,
          chunk_count: 0,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          indexed_at: null,
          metadata: {},
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const form = new FormData();
    form.append("file", new File(["%PDF-1.4"], "a.pdf", { type: "application/pdf" }));

    await apiRequest("/api/v1/documents", { method: "POST", body: form });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBeUndefined();
    expect(headers.Authorization).toBe("Bearer access-token");
    expect(init.body).toBeInstanceOf(FormData);
  });
});
