"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { LoadingState, PageHeader } from "@/components/ui/query-state";
import { Select } from "@/components/ui/select";
import { describeApiError } from "@/lib/api/errors";
import { appConfig } from "@/lib/config";
import { formatBytes } from "@/lib/format";
import { useCollectionsQuery } from "@/lib/hooks/use-collections";
import { useUploadDocumentMutation } from "@/lib/hooks/use-documents";
import { validatePdfUpload } from "@/lib/validation/documents";

export function UploadDocumentForm({ initialCollectionId = "" }: { initialCollectionId?: string }) {
  const router = useRouter();
  const alertRef = useRef<HTMLDivElement>(null);
  const collectionsQuery = useCollectionsQuery();
  const uploadMutation = useUploadDocumentMutation();
  const [file, setFile] = useState<File | null>(null);
  const [collectionId, setCollectionId] = useState(initialCollectionId);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [formErrorRequestId, setFormErrorRequestId] = useState<string | undefined>();
  const [duplicateDocumentId, setDuplicateDocumentId] = useState<string | undefined>();

  function onFileChosen(next: File | null) {
    setFile(next);
    setFormError(null);
    setFormErrorRequestId(undefined);
    setDuplicateDocumentId(undefined);
    if (!next) {
      setValidationError(null);
      return;
    }
    const validated = validatePdfUpload(next);
    setValidationError(validated.ok ? null : validated.message);
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (uploadMutation.isPending) {
      return;
    }
    setFormError(null);
    setFormErrorRequestId(undefined);
    setDuplicateDocumentId(undefined);

    const validated = validatePdfUpload(file);
    if (!validated.ok) {
      setValidationError(validated.message);
      return;
    }
    setValidationError(null);

    try {
      const document = await uploadMutation.mutateAsync({
        file: validated.file,
        collectionId: collectionId.length > 0 ? collectionId : null,
      });
      router.replace(`/documents/${document.id}`);
    } catch (error) {
      const described = describeApiError(error, "Upload failed.");
      setFormError(described.message);
      setFormErrorRequestId(described.requestId);
      setDuplicateDocumentId(described.existingDocumentId);
      queueMicrotask(() => alertRef.current?.focus());
    }
  }

  if (collectionsQuery.isPending && collectionsQuery.data === undefined) {
    return <LoadingState label="Loading upload form" />;
  }

  const collections = collectionsQuery.data ?? [];
  const collectionsFailed = collectionsQuery.isError;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Upload a PDF"
        description={`Files are validated as PDFs and capped at ${appConfig.maxFileSizeMb} MB. Processing starts automatically after upload.`}
        actions={
          <Link
            href="/documents"
            className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-900 hover:bg-slate-50"
          >
            Back to documents
          </Link>
        }
      />

      {collectionsFailed ? (
        <Alert tone="info" title="Collections unavailable">
          <p>
            {describeApiError(collectionsQuery.error).message} You can still upload without
            assigning a collection.
          </p>
          <Button
            variant="secondary"
            className="mt-3"
            onClick={() => void collectionsQuery.refetch()}
          >
            Retry collections
          </Button>
        </Alert>
      ) : null}

      <form
        className="max-w-xl space-y-4 rounded-md border border-slate-200 bg-white p-5"
        onSubmit={(event) => void onSubmit(event)}
        noValidate
      >
        {formError ? (
          <div ref={alertRef} tabIndex={-1} className="outline-none">
            <Alert title="Upload failed" requestId={formErrorRequestId}>
              <p>{formError}</p>
              {duplicateDocumentId ? (
                <p className="mt-2">
                  <Link
                    href={`/documents/${duplicateDocumentId}`}
                    className="font-medium underline-offset-2 hover:underline"
                  >
                    Open existing document
                  </Link>
                </p>
              ) : null}
            </Alert>
          </div>
        ) : null}

        <div className="space-y-1.5">
          <Label htmlFor="upload-file">PDF file</Label>
          <input
            id="upload-file"
            name="file"
            type="file"
            accept="application/pdf,.pdf"
            disabled={uploadMutation.isPending}
            className="block w-full text-sm text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-slate-900 file:px-3 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-slate-800 disabled:opacity-60"
            aria-invalid={validationError !== null ? true : undefined}
            aria-describedby={validationError ? "upload-file-error" : "upload-file-hint"}
            onChange={(event) => {
              onFileChosen(event.target.files?.[0] ?? null);
            }}
            required
          />
          {validationError ? (
            <p id="upload-file-error" className="text-xs text-red-700" role="alert">
              {validationError}
            </p>
          ) : (
            <p id="upload-file-hint" className="text-xs text-slate-500">
              PDF only · max {appConfig.maxFileSizeMb} MB
              {file ? ` · selected ${formatBytes(file.size)}` : null}
            </p>
          )}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="upload-collection">Collection (optional)</Label>
          <Select
            id="upload-collection"
            value={collectionId}
            disabled={uploadMutation.isPending || collectionsFailed || collections.length === 0}
            onChange={(event) => setCollectionId(event.target.value)}
          >
            <option value="">No collection</option>
            {collections.map((collection) => (
              <option key={collection.id} value={collection.id}>
                {collection.name}
              </option>
            ))}
          </Select>
        </div>

        <Button
          type="submit"
          loading={uploadMutation.isPending}
          disabled={validationError !== null}
          className="w-full sm:w-auto"
        >
          {uploadMutation.isPending ? "Uploading…" : "Upload document"}
        </Button>
      </form>
    </div>
  );
}
