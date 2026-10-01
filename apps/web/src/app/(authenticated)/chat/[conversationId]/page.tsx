import { ChatThreadPage } from "@/components/chat/chat-thread";

/** Placeholder path so `output: "export"` emits at least one HTML shell. */
export function generateStaticParams(): { conversationId: string }[] {
  return [{ conversationId: "_" }];
}

export default async function ConversationPage({
  params,
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const { conversationId } = await params;
  return <ChatThreadPage key={conversationId} conversationId={conversationId} />;
}
