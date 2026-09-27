import type { Citation, Message } from "@doculens/shared-types";

const CITATION_MARKER = /\[(\d+)\]/g;

export type AnswerSegment = { kind: "text"; value: string } | { kind: "citation"; order: number };

/**
 * Split assistant answer text on `[n]` markers so the UI can render them as
 * first-class citation controls. Markers are kept as data only — never as HTML.
 */
export function splitAnswerWithCitations(content: string): AnswerSegment[] {
  const segments: AnswerSegment[] = [];
  let lastIndex = 0;
  for (const match of content.matchAll(CITATION_MARKER)) {
    const index = match.index ?? 0;
    if (index > lastIndex) {
      segments.push({ kind: "text", value: content.slice(lastIndex, index) });
    }
    const order = Number.parseInt(match[1] ?? "", 10);
    if (Number.isFinite(order)) {
      segments.push({ kind: "citation", order });
    } else {
      segments.push({ kind: "text", value: match[0] ?? "" });
    }
    lastIndex = index + match[0].length;
  }
  if (lastIndex < content.length) {
    segments.push({ kind: "text", value: content.slice(lastIndex) });
  }
  if (segments.length === 0) {
    segments.push({ kind: "text", value: content });
  }
  return segments;
}

export function citationsByOrder(citations: Citation[]): Map<number, Citation> {
  const map = new Map<number, Citation>();
  for (const citation of [...citations].sort((a, b) => a.citation_order - b.citation_order)) {
    map.set(citation.citation_order, citation);
  }
  return map;
}

export function assistantMessages(messages: Message[]): Message[] {
  return messages.filter((message) => message.role === "ASSISTANT");
}
