import { afterEach, describe, expect, it, vi } from "vitest";
import type { AnswerResponse } from "@doculens/shared-types";

import { askQuestionStream } from "@/lib/api/conversations";
import { ApiError } from "@/lib/api/errors";
import { consumeSseBuffer, parseAnswerStreamData } from "@/lib/chat/sse";
import { setAccessToken } from "@/lib/auth/session";

const finalAnswer = {
  conversation_id: "conv-1",
  outcome: "answered",
  user_message: {
    id: "u1",
    role: "USER",
    content: "What is leave?",
    created_at: "2026-01-01T00:00:00Z",
    citations: [],
  },
  assistant_message: {
    id: "a1",
    role: "ASSISTANT",
    content: "Leave is twenty-five days [1].",
    created_at: "2026-01-01T00:00:01Z",
    citations: [
      {
        id: "c1",
        document_id: "doc-1",
        page_number: 2,
        chunk_id: "chunk-1",
        quoted_text: "Annual leave is twenty-five days.",
        retrieval_score: 0.9,
        reranking_score: 0.8,
        citation_order: 1,
      },
    ],
  },
  retrieval: {
    query: "What is leave?",
    rewritten: false,
    retriever: "hybrid",
    documents_in_scope: 1,
    hits: 1,
    evidence: 1,
    context_items: 1,
    reranking: "applied",
    model: "fake",
    prompt_version: "v1",
    invalid_references: 0,
    truncated: false,
    uncited: false,
  },
  usage: {
    embedding_requests: 1,
    embedding_tokens: 10,
    llm_requests: 1,
    input_tokens: 20,
    output_tokens: 12,
  },
  timing: {
    rewrite_ms: 0,
    retrieval_ms: 1,
    generation_ms: 2,
    persistence_ms: 1,
    total_ms: 4,
  },
} satisfies AnswerResponse;

function sseBody(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream({
    pull(controller) {
      if (index >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(chunks[index]));
      index += 1;
    },
  });
}

describe("parseAnswerStreamData", () => {
  it("parses delta, final, and error frames", () => {
    expect(parseAnswerStreamData('{"type":"delta","text":"Hi"}')).toEqual({
      type: "delta",
      text: "Hi",
    });
    expect(
      parseAnswerStreamData(JSON.stringify({ type: "final", answer: finalAnswer }))?.type,
    ).toBe("final");
    expect(
      parseAnswerStreamData(
        '{"type":"error","error":{"code":"X","message":"nope","request_id":"r1"}}',
      ),
    ).toEqual({
      type: "error",
      error: { code: "X", message: "nope", request_id: "r1" },
    });
  });

  it("ignores malformed frames instead of inventing sources", () => {
    expect(parseAnswerStreamData("not-json")).toBeNull();
    expect(parseAnswerStreamData('{"type":"final","answer":{}}')).toBeNull();
  });
});

describe("consumeSseBuffer", () => {
  it("splits complete SSE events and keeps a partial tail", () => {
    const { events, rest } = consumeSseBuffer(
      'data: {"type":"delta","text":"A"}\n\ndata: {"type":"delta","text":"B"}\n\ndata: {"type":"fi',
    );
    expect(events).toEqual(['{"type":"delta","text":"A"}', '{"type":"delta","text":"B"}']);
    expect(rest).toBe('data: {"type":"fi');
  });
});

describe("askQuestionStream", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it("streams deltas then returns the final persisted answer with citations", async () => {
    setAccessToken("token");
    const deltas: string[] = [];
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: sseBody([
        'data: {"type":"delta","text":"Leave is "}\n\n',
        `data: ${JSON.stringify({ type: "final", answer: finalAnswer })}\n\n`,
      ]),
    });
    vi.stubGlobal("fetch", fetchMock);

    const answer = await askQuestionStream(
      "conv-1",
      { question: "What is leave?" },
      { onDelta: (text) => deltas.push(text) },
    );

    expect(deltas).toEqual(["Leave is "]);
    expect(answer.assistant_message.citations).toHaveLength(1);
    expect(answer.assistant_message.citations[0]?.quoted_text).toBe(
      "Annual leave is twenty-five days.",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/conversations/conv-1/messages/stream"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("treats an interrupted stream as incomplete and surfaces no fabricated final", async () => {
    setAccessToken("token");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: sseBody(['data: {"type":"delta","text":"Partial only"}\n\n']),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(askQuestionStream("conv-1", { question: "What is leave?" })).rejects.toMatchObject(
      {
        code: "STREAM_INCOMPLETE",
      },
    );
  });

  it("propagates AbortError when the caller cancels", async () => {
    setAccessToken("token");
    const controller = new AbortController();
    const fetchMock = vi.fn().mockImplementation((_url, init: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const pending = askQuestionStream(
      "conv-1",
      { question: "What is leave?" },
      { signal: controller.signal },
    );
    controller.abort();
    await expect(pending).rejects.toSatisfy(
      (error: unknown) => error instanceof DOMException && error.name === "AbortError",
    );
  });

  it("supports retry after a failed stream by issuing a fresh request", async () => {
    setAccessToken("token");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        body: sseBody([
          'data: {"type":"error","error":{"code":"LLM_PROVIDER_UNAVAILABLE","message":"down","request_id":"r1"}}\n\n',
        ]),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        body: sseBody([`data: ${JSON.stringify({ type: "final", answer: finalAnswer })}\n\n`]),
      });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      askQuestionStream("conv-1", { question: "What is leave?" }),
    ).rejects.toBeInstanceOf(ApiError);
    const recovered = await askQuestionStream("conv-1", { question: "What is leave?" });
    expect(recovered.outcome).toBe("answered");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("rejects an empty stream body without inventing an answer", async () => {
    setAccessToken("token");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: null,
      }),
    );

    await expect(askQuestionStream("conv-1", { question: "What is leave?" })).rejects.toMatchObject(
      {
        code: "STREAM_EMPTY",
      },
    );
  });

  it("only exposes citations from the final event", async () => {
    setAccessToken("token");
    const deltas: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: sseBody([
          'data: {"type":"delta","text":"See [1]"}\n\n',
          `data: ${JSON.stringify({ type: "final", answer: finalAnswer })}\n\n`,
        ]),
      }),
    );

    const answer = await askQuestionStream(
      "conv-1",
      { question: "What is leave?" },
      { onDelta: (text) => deltas.push(text) },
    );
    expect(deltas.join("")).toBe("See [1]");
    expect(answer.assistant_message.citations.map((c) => c.citation_order)).toEqual([1]);
  });
});
