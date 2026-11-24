"use client";

import { ArrowUp, Square } from "lucide-react";
import { useEffect, useRef, type KeyboardEvent } from "react";

const MAX_CHARS = 2000;

export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  busy,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onStop: () => void;
  busy: boolean;
  disabled: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [value]);

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (!busy && value.trim()) onSend();
    }
  };

  const over = value.length > MAX_CHARS;
  return (
    <form
      className="mx-auto w-full max-w-3xl px-4 pb-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (!busy && value.trim() && !over) onSend();
      }}
    >
      <div className="flex items-end gap-2 rounded-3xl border border-border bg-surface p-2 pl-3 shadow-card focus-within:border-accent/60 focus-within:ring-4 focus-within:ring-accent/10">
        <label htmlFor="composer" className="sr-only">
          Message
        </label>
        <textarea
          id="composer"
          ref={ref}
          rows={1}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={busy ? "Nova is working on it…" : "Ask about billing, refunds, plans or troubleshooting…"}
          className="max-h-[180px] min-h-9 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-subtle disabled:opacity-60"
        />
        {busy ? (
          <button
            type="button"
            onClick={onStop}
            aria-label="Stop generating"
            className="flex size-9 shrink-0 items-center justify-center rounded-full bg-surface-3 text-text hover:opacity-80"
          >
            <Square className="size-3.5 fill-current" />
          </button>
        ) : (
          <button
            type="submit"
            aria-label="Send message"
            disabled={disabled || !value.trim() || over}
            className="flex size-9 shrink-0 items-center justify-center rounded-full bg-accent text-accent-fg transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            <ArrowUp className="size-4" />
          </button>
        )}
      </div>
      <p className="mt-1.5 flex justify-between px-1 text-[11px] text-subtle">
        <span>Enter to send · Shift+Enter for a new line</span>
        <span className={over ? "text-danger" : ""}>
          {value.length > MAX_CHARS * 0.8 ? `${value.length}/${MAX_CHARS}` : ""}
        </span>
      </p>
    </form>
  );
}
