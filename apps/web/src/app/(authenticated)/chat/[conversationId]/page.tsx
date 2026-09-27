import type { Metadata } from "next";

import { ChatThreadPage } from "@/components/chat/chat-thread";

export const metadata: Metadata = {
  title: "Conversation · DocuLens",
};

export default async function ConversationPage({
  params,
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const { conversationId } = await params;
  return <ChatThreadPage key={conversationId} conversationId={conversationId} />;
}
