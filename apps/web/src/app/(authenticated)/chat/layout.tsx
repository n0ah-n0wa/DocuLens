import type { ReactNode } from "react";

import { ChatShell } from "@/components/chat/conversation-sidebar";

export default function ChatLayout({ children }: { children: ReactNode }) {
  return <ChatShell>{children}</ChatShell>;
}
