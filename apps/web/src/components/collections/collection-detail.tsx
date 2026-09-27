"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import type { Collection } from "@doculens/shared-types";

import { DocumentListTable } from "@/components/documents/document-list-table";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { FormField } from "@/components/ui/form-field";
import { Label } from "@/components/ui/label";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui/query-state";
import { Textarea } from "@/components/ui/select";
import { describeApiError, messageForApiError } from "@/lib/api/errors";
import { formatTimestamp } from "@/lib/format";
import {
  useCollectionQuery,
  useDeleteCollectionMutation,
  useUpdateCollectionMutation,
} from "@/lib/hooks/use-collections";
import { useDocumentsQuery } from "@/lib/hooks/use-documents";
import { collectionFormSchema } from "@/lib/validation/documents";

export function CollectionDetailPage({ collectionId }: { collectionId: string }) {
  const collectionQuery = useCollectionQuery(collectionId);
  const documentsQuery = useDocumentsQuery({ collection_id: collectionId });

  if (collectionQuery.isPending && collectionQuery.data === undefined) {
    return <LoadingState label="Loading collection" />;
  }

  if (collectionQuery.isError || !collectionQuery.data) {
    return (
      <div className="space-y-4">
        <PageHeader title="Collection" />
        <ErrorState
          title="Collection unavailable"
          message={messageForApiError(collectionQuery.error, "The collection was not found.")}
          action={
            <Link
              href="/collections"
              className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-900 hover:bg-slate-50"
            >
              Back to collections
            </Link>
          }
        />
      </div>
    );
  }

  const collection = collectionQuery.data;

  return (
    <div className="space-y-6">
      <PageHeader
        title={collection.name}
        description={`Created ${formatTimestamp(collection.created_at)} · Updated ${formatTimestamp(collection.updated_at)}`}
        actions={
          <Link
            href="/collections"
            className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-900 hover:bg-slate-50"
          >
            All collections
          </Link>
        }
      />

      <CollectionEditForm key={collection.id} collection={collection} />

      <section className="space-y-3" aria-labelledby="collection-documents-heading">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <h2 id="collection-documents-heading" className="text-sm font-semibold text-slate-900">
            Documents in this collection
          </h2>
          <Link
            href={`/documents/upload?collection_id=${collection.id}`}
            className="text-sm font-medium text-slate-900 underline-offset-2 hover:underline"
          >
            Upload into collection
          </Link>
        </div>
        {documentsQuery.isPending && documentsQuery.data === undefined ? (
          <LoadingState label="Loading documents" />
        ) : documentsQuery.isError && documentsQuery.data === undefined ? (
          <ErrorState
            title="Could not load documents"
            message={messageForApiError(documentsQuery.error)}
            action={
              <Button variant="secondary" onClick={() => void documentsQuery.refetch()}>
                Try again
              </Button>
            }
          />
        ) : (
          <DocumentListTable
            documents={documentsQuery.data ?? []}
            collections={[collection]}
            emptyTitle="No documents in this collection"
            emptyMessage="Upload a PDF into this collection or move an existing document here."
            emptyAction={
              <Link
                href={`/documents/upload?collection_id=${collection.id}`}
                className="inline-flex items-center justify-center rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
              >
                Upload PDF
              </Link>
            }
          />
        )}
      </section>
    </div>
  );
}

function CollectionEditForm({ collection }: { collection: Collection }) {
  const router = useRouter();
  const alertRef = useRef<HTMLDivElement>(null);
  const updateMutation = useUpdateCollectionMutation(collection.id);
  const deleteMutation = useDeleteCollectionMutation();
  const [name, setName] = useState(collection.name);
  const [description, setDescription] = useState(collection.description ?? "");
  const [nameError, setNameError] = useState<string | undefined>();
  const [formError, setFormError] = useState<string | null>(null);
  const [formErrorRequestId, setFormErrorRequestId] = useState<string | undefined>();
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const busy = updateMutation.isPending || deleteMutation.isPending;

  async function onSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) {
      return;
    }
    setFormError(null);
    setFormErrorRequestId(undefined);
    setNotice(null);
    setNameError(undefined);

    const parsed = collectionFormSchema.safeParse({
      name,
      description: description.trim().length > 0 ? description : undefined,
    });
    if (!parsed.success) {
      const issue = parsed.error.issues.find((item) => item.path[0] === "name");
      setNameError(issue?.message ?? "Invalid collection.");
      return;
    }

    const nextDescription = parsed.data.description ?? null;
    const body: { name?: string; description?: string | null } = {};
    if (parsed.data.name !== collection.name) {
      body.name = parsed.data.name;
    }
    if (nextDescription !== collection.description) {
      body.description = nextDescription;
    }
    if (body.name === undefined && body.description === undefined) {
      setNotice("No changes to save.");
      return;
    }

    try {
      await updateMutation.mutateAsync(body);
      setNotice("Collection updated.");
    } catch (error) {
      const described = describeApiError(error, "Could not update the collection.");
      setFormError(described.message);
      setFormErrorRequestId(described.requestId);
      queueMicrotask(() => alertRef.current?.focus());
    }
  }

  return (
    <section
      className="max-w-xl space-y-4 rounded-md border border-slate-200 bg-white p-5"
      aria-labelledby="edit-collection-heading"
    >
      <h2 id="edit-collection-heading" className="text-sm font-semibold text-slate-900">
        Edit collection
      </h2>
      {formError ? (
        <div ref={alertRef} tabIndex={-1} className="outline-none">
          <Alert title="Update failed" requestId={formErrorRequestId}>
            {formError}
          </Alert>
        </div>
      ) : null}
      {notice ? <Alert tone="info">{notice}</Alert> : null}
      <form className="space-y-4" onSubmit={(event) => void onSave(event)} noValidate>
        <FormField
          id="edit-collection-name"
          label="Name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          error={nameError}
          maxLength={200}
          required
          disabled={busy}
        />
        <div className="space-y-1.5">
          <Label htmlFor="edit-collection-description">Description</Label>
          <Textarea
            id="edit-collection-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={3}
            maxLength={2000}
            disabled={busy}
          />
          <p className="text-xs text-slate-500">Leave empty and save to clear the description.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="submit" loading={updateMutation.isPending} disabled={busy}>
            Save changes
          </Button>
          <Button
            type="button"
            variant="danger"
            disabled={busy}
            onClick={() => setConfirmDelete(true)}
          >
            Delete collection
          </Button>
        </div>
      </form>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete collection?"
        description={`Delete “${collection.name}”? Documents stay in your library; they are only detached from this collection.`}
        confirmLabel="Delete collection"
        tone="danger"
        busy={deleteMutation.isPending}
        onCancel={() => {
          if (!deleteMutation.isPending) {
            setConfirmDelete(false);
          }
        }}
        onConfirm={() => {
          if (deleteMutation.isPending) {
            return;
          }
          void (async () => {
            try {
              await deleteMutation.mutateAsync(collection.id);
              setConfirmDelete(false);
              router.replace("/collections");
            } catch (error) {
              const described = describeApiError(error, "Could not delete the collection.");
              setConfirmDelete(false);
              setFormError(described.message);
              setFormErrorRequestId(described.requestId);
              queueMicrotask(() => alertRef.current?.focus());
            }
          })();
        }}
      />
    </section>
  );
}
