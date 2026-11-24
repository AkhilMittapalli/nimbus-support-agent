import { BookOpen } from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const CITE_PREFIX = "#cite:";

/** `[refund-policy#Refund limits by tier]` -> a markdown link the renderer turns into a citation chip. */
export function linkifyCitations(text: string): string {
  return text.replace(/\[([a-z0-9][a-z0-9-]*)#([^\]\n]{1,80})\](?!\()/g, (_m, doc: string, section: string) => {
    return `[${section.trim()}](${CITE_PREFIX}${encodeURIComponent(doc)})`;
  });
}

const components: Components = {
  a({ href, children }) {
    if (href?.startsWith(CITE_PREFIX)) {
      const doc = decodeURIComponent(href.slice(CITE_PREFIX.length));
      return (
        <span
          title={`Source: ${doc}`}
          className="mx-0.5 inline-flex items-center gap-1 rounded-md border border-border bg-surface-2 px-1.5 align-[1px] text-[11px] font-medium text-muted"
        >
          <BookOpen className="size-3 shrink-0 text-teal" aria-hidden />
          <span className="font-mono text-subtle">{doc}</span>
          <span aria-hidden>›</span>
          {children}
        </span>
      );
    }
    return (
      <a href={href} target="_blank" rel="noreferrer noopener">
        {children}
      </a>
    );
  },
};

export function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {linkifyCitations(text)}
    </ReactMarkdown>
  );
}
