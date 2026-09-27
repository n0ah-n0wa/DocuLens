"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { FormField } from "@/components/ui/form-field";
import { Label } from "@/components/ui/label";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/ui/query-state";
import { Textarea } from "@/components/ui/select";
import { describeApiError, messageForApiError } from "@/lib/api/errors";
import { formatTimestamp } from "@/lib/format";
import {
  useCollectionsQuery,
  useCreateCollectionMutation,
  useDeleteCollectionMutation,
} from "@/lib/hooks/use-collections";
import { collectionFormSchema } from "@/lib/validation/documents";

export function CollectionsPageClient() {
  const collectionsQuery = useCollectionsQuery();
  const createMutation = useCreateCollectionMutation();
  const deleteMutation = useDeleteCollectionMutation();
  const router = useRouter();
  const alertRef = useRef<HTMLDivElement>(null);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [nameError, setNameError] = useState<string | undefined>();
  const [formError, setFormError] = useState<string | null>(null);
  const [formErrorRequestId, setFormErrorRequestId] = useState<string | undefined>();
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  const pendingDelete = (collectionsQuery.data ?? []).find(
    (collection) => collection.id === pendingDeleteId,
  );

  async function onCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (createMutation.isPending) {
      return;
    }
    setFormError(null);
    setFormErrorRequestId(undefined);
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

    try {
      const collection = await createMutation.mutateAsync({
        name: parsed.data.name,
        ...(parsed.data.description !== undefined ? { description: parsed.data.description } : {}),
      });
      setName("");
      setDescription("");
      router.push(`/collections/${collection.id}`);
    } catch (error) {
      const described = describeApiError(error, "Could not create the collection.");
      setFormError(described.message);
      setFormErrorRequestId(described.requestId);
      queueMicrotask(() => alertRef.current?.focus());
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Collections"
        description="Group documents for filtering and later conversation scope. Deleting a collection detaches documents; it does not delete them."
      />

      <section
        className="max-w-xl space-y-4 rounded-md border border-slate-200 bg-white p-5"
        aria-labelledby="create-collection-heading"
      >
        <h2 id="create-collection-heading" className="text-sm font-semibold text-slate-900">
          Create collection
        </h2>
        {formError ? (
          <div ref={alertRef} tabIndex={-1} className="outline-none">
            <Alert title="Action failed" requestId={formErrorRequestId}>
              {formError}
            </Alert>
          </div>
        ) : null}
        <form className="space-y-4" onSubmit={(event) => void onCreate(event)} noValidate>
          <FormField
            id="collection-name"
            label="Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            error={nameError}
            maxLength={200}
            required
          />
          <div className="space-y-1.5">
            <Label htmlFor="collection-description">Description (optional)</Label>
            <Textarea
              id="collection-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={3}
              maxLength={2000}
            />
          </div>
          <Button type="submit" loading={createMutation.isPending}>
            {createMutation.isPending ? "Creating…" : "Create collection"}
          </Button>
        </form>
      </section>

      {collectionsQuery.isPending && collectionsQuery.data === undefined ? (
        <LoadingState label="Loading collections" />
      ) : collectionsQuery.isError && collectionsQuery.data === undefined ? (
        <ErrorState
          title="Could not load collections"
          message={messageForApiError(collectionsQuery.error)}
          action={
            <Button variant="secondary" onClick={() => void collectionsQuery.refetch()}>
              Try again
            </Button>
          }
        />
      ) : (collectionsQuery.data?.length ?? 0) === 0 ? (
        <EmptyState
          title="No collections yet"
          message="Create a collection to organise uploaded documents."
        />
      ) : (
        <ul className="divide-y divide-slate-100 overflow-hidden rounded-md border border-slate-200 bg-white">
          {(collectionsQuery.data ?? []).map((collection) => (
            <li
              key={collection.id}
              className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0 space-y-1">
                <Link
                  href={`/collections/${collection.id}`}
                  className="font-medium break-words text-slate-900 underline-offset-2 hover:underline"
                >
                  {collection.name}
                </Link>
                {collection.description ? (
                  <p className="line-clamp-2 text-sm text-slate-600">{collection.description}</p>
                ) : null}
                <p className="text-xs text-slate-500">
                  Updated {formatTimestamp(collection.updated_at)}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Link
                  href={`/documents?collection_id=${collection.id}`}
                  className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-900 hover:bg-slate-50"
                >
                  View documents
                </Link>
                <Button
                  variant="danger"
                  loading={deleteMutation.isPending && deleteMutation.variables === collection.id}
                  disabled={deleteMutation.isPending}
                  onClick={() => setPendingDeleteId(collection.id)}
                >
                  Delete
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <ConfirmDialog
        open={pendingDeleteId !== null}
        title="Delete collection?"
        description={
          pendingDelete
            ? `Delete “${pendingDelete.name}”? Documents stay in your library; they are only detached from this collection.`
            : "Delete this collection? Documents stay in your library."
        }
        confirmLabel="Delete collection"
        tone="danger"
        busy={deleteMutation.isPending}
        onCancel={() => {
          if (!deleteMutation.isPending) {
            setPendingDeleteId(null);
          }
        }}
        onConfirm={() => {
          if (!pendingDeleteId || deleteMutation.isPending) {
            return;
          }
          void (async () => {
            try {
              await deleteMutation.mutateAsync(pendingDeleteId);
              setPendingDeleteId(null);
            } catch (error) {
              const described = describeApiError(error, "Could not delete the collection.");
              setPendingDeleteId(null);
              setFormError(described.message);
              setFormErrorRequestId(described.requestId);
              queueMicrotask(() => alertRef.current?.focus());
            }
          })();
        }}
      />
    </div>
  );
}
