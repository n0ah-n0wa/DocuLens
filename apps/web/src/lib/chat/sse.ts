import type { AnswerResponse, AnswerStreamEvent, ApiErrorBody } from "@doculens/shared-types";

/**
 * Parse one SSE `data:` JSON payload from the answer stream.
 * Unknown or malformed frames are ignored so a noisy proxy cannot invent citations.
 */
export function parseAnswerStreamData(raw: string): AnswerStreamEvent | null {
  const trimmed = raw.trim();
  if (!trimmed || trimmed === "[DONE]") {
    return null;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed) as unknown;
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || !("type" in parsed)) {
    return null;
  }
  const record = parsed as Record<string, unknown>;
  if (record.type === "delta" && typeof record.text === "string") {
    return { type: "delta", text: record.text };
  }
  if (record.type === "final" && isAnswerLike(record.answer)) {
    return { type: "final", answer: record.answer };
  }
  if (record.type === "error" && isErrorBody(record.error)) {
    return { type: "error", error: record.error };
  }
  return null;
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.code === "string" &&
    typeof record.message === "string" &&
    typeof record.request_id === "string"
  );
}

function isAnswerLike(value: unknown): value is AnswerResponse {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  if (
    typeof record.conversation_id !== "string" ||
    typeof record.outcome !== "string" ||
    !isMessageLike(record.user_message) ||
    !isMessageLike(record.assistant_message)
  ) {
    return false;
  }
  return true;
}

function isMessageLike(value: unknown): boolean {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.id === "string" &&
    typeof record.role === "string" &&
    typeof record.content === "string" &&
    typeof record.created_at === "string" &&
    Array.isArray(record.citations)
  );
}

/**
 * Split an SSE buffer into complete events and the remaining incomplete tail.
 */
export function consumeSseBuffer(buffer: string): { events: string[]; rest: string } {
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  const events: string[] = [];
  for (const block of parts) {
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    if (dataLines.length > 0) {
      events.push(dataLines.join("\n"));
    }
  }
  return { events, rest };
}
