import { z } from "zod";

import { appConfig } from "@/lib/config";
import { maxUploadBytes } from "@/lib/format";

export const MAX_FILENAME_LENGTH = 255;
export const MAX_COLLECTION_NAME_LENGTH = 200;
export const MAX_COLLECTION_DESCRIPTION_LENGTH = 2000;

export const collectionFormSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Enter a collection name.")
    .max(MAX_COLLECTION_NAME_LENGTH, "Name is too long."),
  description: z
    .string()
    .trim()
    .max(MAX_COLLECTION_DESCRIPTION_LENGTH, "Description is too long.")
    .optional(),
});

export type CollectionFormValues = z.infer<typeof collectionFormSchema>;

export const renameDocumentSchema = z.object({
  filename: z
    .string()
    .trim()
    .min(1, "Enter a filename.")
    .max(MAX_FILENAME_LENGTH, "Filename is too long.")
    .refine((value) => value.toLowerCase().endsWith(".pdf"), {
      message: "Filename must end with .pdf.",
    }),
});

export type UploadValidationResult = { ok: true; file: File } | { ok: false; message: string };

export function validatePdfUpload(file: File | null | undefined): UploadValidationResult {
  if (!file) {
    return { ok: false, message: "Choose a PDF file to upload." };
  }
  const name = file.name.trim();
  if (!name.toLowerCase().endsWith(".pdf")) {
    return { ok: false, message: "Only PDF files are supported." };
  }
  if (name.length > MAX_FILENAME_LENGTH) {
    return { ok: false, message: "Filename is too long." };
  }
  if (file.size <= 0) {
    return { ok: false, message: "The selected file is empty." };
  }
  if (file.size > maxUploadBytes()) {
    return {
      ok: false,
      message: `File must be at most ${appConfig.maxFileSizeMb} MB.`,
    };
  }
  const mime = file.type.trim().toLowerCase();
  if (mime.length > 0 && mime !== "application/pdf") {
    return { ok: false, message: "The file must be a PDF (application/pdf)." };
  }
  return { ok: true, file };
}
