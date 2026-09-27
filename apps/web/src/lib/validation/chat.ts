import { z } from "zod";

export const MAX_CONVERSATION_TITLE_LENGTH = 200;
/** Matches the API transport max; the deployed effective cap may be lower (default 2000). */
export const MAX_QUESTION_LENGTH = 100_000;

export const createConversationSchema = z.object({
  title: z
    .string()
    .trim()
    .min(1, "Enter a conversation title.")
    .max(MAX_CONVERSATION_TITLE_LENGTH, "Title is too long."),
  collectionId: z.string().optional(),
});

export const askQuestionSchema = z.object({
  question: z
    .string()
    .trim()
    .min(1, "Enter a question.")
    .max(MAX_QUESTION_LENGTH, "Question is too long."),
});
