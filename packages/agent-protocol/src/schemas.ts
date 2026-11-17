/**
 * Agent response protocol v1. Mirrors services/common/src/wta_common/protocol.py; the shared
 * fixtures in ../fixtures are validated by both implementations so they cannot drift.
 *
 * UI output is untrusted: strict objects (unknown properties are rejected), a closed set of
 * component types, http(s)-only URLs, explicit action types, and bounded depth/size.
 */
import { z } from "zod";

export const PROTOCOL_VERSION = "1";
export const UI_VERSION = "1";
export const MAX_UI_DEPTH = 4;
export const MAX_UI_NODES = 40;
export const MAX_MESSAGE_CHARS = 2000;

export function isHttpUrl(value: string): boolean {
  try {
    const u = new URL(value);
    return (u.protocol === "http:" || u.protocol === "https:") && u.hostname !== "" && !u.username && !u.password;
  } catch {
    return false;
  }
}

const httpUrl = z.string().max(2048).refine(isHttpUrl, { message: "url must be http(s) without credentials" });

// --- Actions ------------------------------------------------------------------------------
export const OpenUrlAction = z.strictObject({ type: z.literal("open_url"), url: httpUrl });
export const SendMessageAction = z.strictObject({
  type: z.literal("send_message"),
  text: z.string().min(1).max(200),
});
export const Action = z.discriminatedUnion("type", [OpenUrlAction, SendMessageAction]);

// --- UI components (allowlist) -------------------------------------------------------------
export const TextNode = z.strictObject({ type: z.literal("text"), text: z.string().max(2000) });
export const LinkNode = z.strictObject({
  type: z.literal("link"),
  label: z.string().min(1).max(120),
  url: httpUrl,
});
export const ButtonNode = z.strictObject({
  type: z.literal("button"),
  label: z.string().min(1).max(120),
  action: Action,
});
export const ListNode = z.strictObject({
  type: z.literal("list"),
  ordered: z.boolean().default(false),
  get items() {
    return z.array(UiNode).max(10);
  },
});
export const CardNode = z.strictObject({
  type: z.literal("card"),
  title: z.string().max(120).nullish(),
  get children() {
    return z.array(UiNode).max(10);
  },
});
export const UiNode = z.discriminatedUnion("type", [TextNode, LinkNode, ButtonNode, ListNode, CardNode]);

export type UiNodeT = z.infer<typeof UiNode>;
export type ActionT = z.infer<typeof Action>;

export function measureUi(node: UiNodeT, depth = 1): { depth: number; count: number } {
  let maxDepth = depth;
  let count = 1;
  const children = node.type === "list" ? node.items : node.type === "card" ? node.children : [];
  for (const child of children) {
    const m = measureUi(child, depth + 1);
    maxDepth = Math.max(maxDepth, m.depth);
    count += m.count;
  }
  return { depth: maxDepth, count };
}

export const UiDocument = z.strictObject({ version: z.literal(UI_VERSION), root: UiNode }).refine(
  (doc) => {
    const m = measureUi(doc.root);
    return m.depth <= MAX_UI_DEPTH && m.count <= MAX_UI_NODES;
  },
  { message: "ui tree too deep or too large" },
);

// --- Requests / responses --------------------------------------------------------------------
export const ChatRequest = z.strictObject({
  message: z
    .string()
    .min(1)
    .max(MAX_MESSAGE_CHARS)
    .refine((s) => s.trim().length > 0, { message: "message must not be blank" }),
  conversation_id: z.uuid().nullish(),
});

export const Source = z.strictObject({
  id: z.string().max(32),
  title: z.string().max(300),
  url: httpUrl,
  section: z.string().max(300).nullish(),
  excerpt: z.string().max(500).nullish(),
});

export const UsageInfo = z.strictObject({
  input_tokens: z.number().int().min(0),
  output_tokens: z.number().int().min(0),
});

export const ChatResponse = z.strictObject({
  protocol_version: z.literal(PROTOCOL_VERSION),
  conversation_id: z.uuid(),
  message_id: z.uuid(),
  answer: z.string().max(8000),
  sources: z.array(Source).max(10).default([]),
  ui: UiDocument.nullish(),
  actions: z.array(Action).max(5).default([]),
  usage: UsageInfo.nullish(),
});

export const ErrorResponse = z.strictObject({
  error: z.strictObject({
    code: z.string(),
    message: z.string(),
    request_id: z.string().nullish(),
  }),
});

export const AgentPublicConfig = z.strictObject({
  protocol_version: z.literal(PROTOCOL_VERSION),
  name: z.string(),
});

export type ChatResponseT = z.infer<typeof ChatResponse>;
export type SourceT = z.infer<typeof Source>;
export type ErrorResponseT = z.infer<typeof ErrorResponse>;
