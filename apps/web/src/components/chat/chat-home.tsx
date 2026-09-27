"use client";

import { EmptyState, PageHeader } from "@/components/ui/query-state";

export function ChatHome() {
  return (
    <div className="space-y-4">
      <PageHeader
        title="Chat"
        description="Ask grounded questions. Answers include separate citation evidence from your documents."
      />
      <EmptyState
        title="Select or create a conversation"
        message="Use New in the sidebar to start a chat. Optionally attach a collection as the default retrieval scope."
      />
    </div>
  );
}
