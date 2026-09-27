"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useId, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { FormField } from "@/components/ui/form-field";
import { Label } from "@/components/ui/label";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/query-state";
import { Select } from "@/components/ui/select";
import { describeApiError, messageForApiError } from "@/lib/api/errors";
import { formatTimestamp } from "@/lib/format";
import { useCollectionsQuery } from "@/lib/hooks/use-collections";
import {
  useConversationsQuery,
  useCreateConversationMutation,
  useDeleteConversationMutation,
} from "@/lib/hooks/use-conversations";
import { createConversationSchema } from "@/lib/validation/chat";

export function ChatShell({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-[70vh] gap-4 lg:grid-cols-[18rem_minmax(0,1fr)] xl:grid-cols-[20rem_minmax(0,1fr)]">
      <ConversationSidebar />
      <div className="min-w-0 min-h-0">{children}</div>
    </div>
  );
}

function ConversationSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const conversationsQuery = useConversationsQuery();
  const collectionsQuery = useCollectionsQuery();
  const createMutation = useCreateConversationMutation();
  const deleteMutation = useDeleteConversationMutation();
  const alertRef = useRef<HTMLDivElement>(null);
  const createFormId = useId();
  const listId = useId();

  const [title, setTitle] = useState("");
  const [collectionId, setCollectionId] = useState("");
  const [titleError, setTitleError] = useState<string | undefined>();
  const [formError, setFormError] = useState<string | null>(null);
  const [formErrorRequestId, setFormErrorRequestId] = useState<string | undefined>();
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [listOpen, setListOpen] = useState(false);

  const collections = collectionsQuery.data ?? [];
  const conversations = conversationsQuery.data ?? [];
  const pendingDelete = conversations.find((item) => item.id === pendingDeleteId);
  const collectionName = new Map(collections.map((c) => [c.id, c.name]));

  async function onCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (createMutation.isPending) {
      return;
    }
    setFormError(null);
    setFormErrorRequestId(undefined);
    setTitleError(undefined);

    const parsed = createConversationSchema.safeParse({
      title,
      ...(collectionId ? { collectionId } : {}),
    });
    if (!parsed.success) {
      setTitleError(parsed.error.issues[0]?.message ?? "Invalid title.");
      return;
    }

    try {
      const conversation = await createMutation.mutateAsync({
        title: parsed.data.title,
        ...(collectionId ? { collection_id: collectionId } : {}),
      });
      setTitle("");
      setCollectionId("");
      setShowCreate(false);
      router.push(`/chat/${conversation.id}`);
    } catch (error) {
      const described = describeApiError(error, "Could not create the conversation.");
      setFormError(described.message);
      setFormErrorRequestId(described.requestId);
      queueMicrotask(() => alertRef.current?.focus());
    }
  }

  return (
    <aside className="flex flex-col gap-3 rounded-md border border-slate-200 bg-white p-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900">Conversations</h2>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="secondary"
            className="px-2 py-1 text-xs lg:hidden"
            aria-expanded={listOpen}
            aria-controls={listId}
            onClick={() => setListOpen((open) => !open)}
          >
            {listOpen ? "Hide list" : "Show list"}
          </Button>
          <Button
            type="button"
            variant="secondary"
            className="px-2 py-1 text-xs"
            aria-expanded={showCreate}
            aria-controls={createFormId}
            onClick={() => setShowCreate((open) => !open)}
          >
            {showCreate ? "Cancel" : "New"}
          </Button>
        </div>
      </div>

      {showCreate ? (
        <form
          id={createFormId}
          className="space-y-3 rounded-md border border-slate-100 bg-slate-50 p-3"
          onSubmit={(event) => void onCreate(event)}
          noValidate
        >
          {formError ? (
            <div ref={alertRef} tabIndex={-1} className="outline-none">
              <Alert title="Create failed" requestId={formErrorRequestId}>
                {formError}
              </Alert>
            </div>
          ) : null}
          <FormField
            id="conversation-title"
            label="Title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            error={titleError}
            maxLength={200}
            required
            autoFocus
          />
          <div className="space-y-1.5">
            <Label htmlFor="conversation-collection">Collection scope (optional)</Label>
            <Select
              id="conversation-collection"
              value={collectionId}
              disabled={collectionsQuery.isError}
              onChange={(event) => setCollectionId(event.target.value)}
            >
              <option value="">All READY documents</option>
              {collections.map((collection) => (
                <option key={collection.id} value={collection.id}>
                  {collection.name}
                </option>
              ))}
            </Select>
            <p className="text-xs text-slate-500">
              Collection scope applies when a question does not select specific documents.
            </p>
          </div>
          <Button type="submit" loading={createMutation.isPending} className="w-full">
            {createMutation.isPending ? "Creating…" : "Create conversation"}
          </Button>
        </form>
      ) : null}

      <div id={listId} className={`${listOpen ? "block" : "hidden"} lg:block`}>
        {conversationsQuery.isPending && conversationsQuery.data === undefined ? (
          <LoadingState label="Loading conversations" />
        ) : conversationsQuery.isError && conversationsQuery.data === undefined ? (
          <ErrorState
            title="Could not load conversations"
            message={messageForApiError(conversationsQuery.error)}
            action={
              <Button variant="secondary" onClick={() => void conversationsQuery.refetch()}>
                Try again
              </Button>
            }
          />
        ) : conversations.length === 0 ? (
          <EmptyState
            title="No conversations yet"
            message="Create a conversation to ask grounded questions about your documents."
          />
        ) : (
          <ul
            className="max-h-[40vh] space-y-1 overflow-y-auto lg:max-h-[60vh]"
            aria-label="Conversation list"
          >
            {conversations.map((conversation) => {
              const href = `/chat/${conversation.id}`;
              const active = pathname === href;
              return (
                <li key={conversation.id} className="group relative">
                  <Link
                    href={href}
                    className={`block rounded-md px-3 py-2 pr-16 text-sm ${
                      active ? "bg-slate-900 text-white" : "text-slate-800 hover:bg-slate-100"
                    }`}
                    aria-current={active ? "page" : undefined}
                    onClick={() => setListOpen(false)}
                  >
                    <span className="line-clamp-2 font-medium break-words">
                      {conversation.title}
                    </span>
                    <span
                      className={`mt-1 block text-xs ${active ? "text-slate-300" : "text-slate-500"}`}
                    >
                      {conversation.collection_id
                        ? (collectionName.get(conversation.collection_id) ?? "Collection scope")
                        : "All documents"}
                      {" · "}
                      {formatTimestamp(conversation.updated_at)}
                    </span>
                  </Link>
                  <Button
                    type="button"
                    variant="ghost"
                    className={`absolute top-1 right-1 px-2 py-1 text-xs ${
                      active ? "text-white hover:bg-slate-800" : ""
                    }`}
                    aria-label={`Delete conversation ${conversation.title}`}
                    onClick={() => setPendingDeleteId(conversation.id)}
                  >
                    Delete
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <ConfirmDialog
        open={pendingDeleteId !== null}
        title="Delete conversation?"
        description={
          pendingDelete
            ? `Delete “${pendingDelete.title}”? Message history for this conversation will be removed.`
            : "Delete this conversation?"
        }
        confirmLabel="Delete conversation"
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
              if (pathname === `/chat/${pendingDeleteId}`) {
                router.replace("/chat");
              }
            } catch (error) {
              const described = describeApiError(error, "Could not delete the conversation.");
              setPendingDeleteId(null);
              setFormError(described.message);
              setFormErrorRequestId(described.requestId);
              setShowCreate(true);
              queueMicrotask(() => alertRef.current?.focus());
            }
          })();
        }}
      />
    </aside>
  );
}
