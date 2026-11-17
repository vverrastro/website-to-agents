"use client";
import { type ActionT, isHttpUrl, UiDocument, type UiNodeT } from "@wta/agent-protocol";

/**
 * Renders the validated UI tree. Only allowlisted components exist here; anything else is
 * rejected before this point. Text is always rendered as text (never HTML), links are re-checked
 * and open with rel=noopener noreferrer.
 */
export function RenderUi({ ui, onAction }: { ui: unknown; onAction: (action: ActionT) => void }) {
  const parsed = UiDocument.safeParse(ui);
  if (!parsed.success) return null; // fail closed: the text answer and sources remain usable
  return <Node node={parsed.data.root} onAction={onAction} />;
}

function Node({ node, onAction }: { node: UiNodeT; onAction: (a: ActionT) => void }) {
  switch (node.type) {
    case "text":
      return <p className="text-sm">{node.text}</p>;
    case "link":
      return isHttpUrl(node.url) ? (
        <a
          href={node.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm text-accent underline underline-offset-2"
        >
          {node.label}
        </a>
      ) : null;
    case "button":
      return (
        <button
          type="button"
          onClick={() => onAction(node.action)}
          className="rounded-full border border-line bg-paper px-3 py-1 text-sm hover:bg-accent-soft"
        >
          {node.label}
        </button>
      );
    case "list": {
      const Tag = node.ordered ? "ol" : "ul";
      return (
        <Tag className={`flex flex-wrap gap-2 ${node.ordered ? "list-decimal pl-5" : ""}`}>
          {node.items.map((item, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: static tree, never reordered
            <li key={i} className="list-none">
              <Node node={item} onAction={onAction} />
            </li>
          ))}
        </Tag>
      );
    }
    case "card":
      return (
        <div className="space-y-2 rounded-lg border border-line p-3">
          {node.title ? <p className="text-xs font-semibold uppercase tracking-wide text-muted">{node.title}</p> : null}
          {node.children.map((c, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: static tree, never reordered
            <Node key={i} node={c} onAction={onAction} />
          ))}
        </div>
      );
  }
}
