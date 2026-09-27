import type { Metadata } from "next";

import { ChatHome } from "@/components/chat/chat-home";

export const metadata: Metadata = {
  title: "Chat · DocuLens",
};

export default function ChatPage() {
  return <ChatHome />;
}
