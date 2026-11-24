"use client";

import { useEffect, useRef } from "react";

import type { TimelineItem } from "@/lib/conversation";

import { AssistantBubble, UserBubble } from "./MessageBubble";

/** Scrollable conversation (chat + voice). Auto-scrolls while the user is near the bottom. */
export function Timeline({
  items,
  onRetry,
  onShowTrace,
}: {
  items: TimelineItem[];
  onRetry: (id: string) => void;
  onShowTrace: (turnId: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  }, [items]);

  return (
    <div
      ref={scrollRef}
      onScroll={(e) => {
        const el = e.currentTarget;
        pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      }}
      className="scrollbar-thin h-full overflow-y-auto"
      aria-live="polite"
      aria-label="Conversation"
    >
      <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6">
        {items.map((item) =>
          item.kind === "user" ? (
            <UserBubble key={item.id} item={item} />
          ) : (
            <AssistantBubble key={item.id} item={item} onRetry={onRetry} onShowTrace={onShowTrace} />
          ),
        )}
      </div>
    </div>
  );
}
