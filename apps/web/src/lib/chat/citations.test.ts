import { describe, expect, it } from "vitest";
import type { Citation } from "@doculens/shared-types";

import { citationsByOrder, splitAnswerWithCitations } from "@/lib/chat/citations";
import { askQuestionSchema, createConversationSchema } from "@/lib/validation/chat";

describe("splitAnswerWithCitations", () => {
  it("returns plain text when there are no markers", () => {
    expect(splitAnswerWithCitations("No citations here.")).toEqual([
      { kind: "text", value: "No citations here." },
    ]);
  });

  it("splits markers into first-class citation segments", () => {
    expect(splitAnswerWithCitations("See [1] and also [2].")).toEqual([
      { kind: "text", value: "See " },
      { kind: "citation", order: 1 },
      { kind: "text", value: " and also " },
      { kind: "citation", order: 2 },
      { kind: "text", value: "." },
    ]);
  });

  it("handles adjacent markers and leading markers", () => {
    expect(splitAnswerWithCitations("[1][2] done")).toEqual([
      { kind: "citation", order: 1 },
      { kind: "citation", order: 2 },
      { kind: "text", value: " done" },
    ]);
  });
});

describe("citationsByOrder", () => {
  it("indexes by citation_order without inventing fields", () => {
    const citations: Citation[] = [
      {
        id: "c2",
        document_id: "d1",
        page_number: 3,
        chunk_id: null,
        quoted_text: "second",
        retrieval_score: 0.5,
        reranking_score: null,
        citation_order: 2,
      },
      {
        id: "c1",
        document_id: "d1",
        page_number: 1,
        chunk_id: "chunk-1",
        quoted_text: "first",
        retrieval_score: 0.9,
        reranking_score: 0.8,
        citation_order: 1,
      },
    ];
    const map = citationsByOrder(citations);
    expect(map.get(1)?.quoted_text).toBe("first");
    expect(map.get(2)?.quoted_text).toBe("second");
    expect(map.size).toBe(2);
  });
});

describe("chat validation", () => {
  it("requires a non-empty trimmed title", () => {
    expect(createConversationSchema.safeParse({ title: "  " }).success).toBe(false);
    expect(createConversationSchema.safeParse({ title: "Q3 review" }).success).toBe(true);
  });

  it("requires a non-empty trimmed question", () => {
    expect(askQuestionSchema.safeParse({ question: "" }).success).toBe(false);
    expect(askQuestionSchema.safeParse({ question: " What is X? " }).success).toBe(true);
  });
});
