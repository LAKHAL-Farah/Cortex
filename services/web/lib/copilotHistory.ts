import type { AgentName, AgentRawData, ChatMessage, ChatSource } from "@/lib/types";

// -- Copilot conversation history -------------------------------------------
//
// Persisted server-side (see services/api/app/routers/conversations.py)
// rather than in this browser's localStorage, so history survives across
// devices. Cortex has no login system, so there's no real account to scope
// by -- instead each browser keeps a random UUID (CLIENT_ID_KEY below) and
// sends it as X-Client-Id on every request. That id doubles as the "sync
// code": copying it into another browser's storage (see setClientId) makes
// that browser see the same history, the same way knowing an API key grants
// access elsewhere in this app -- just one level more granular.

export interface StoredMessage extends ChatMessage {
  sources?: ChatSource[];
  errored?: boolean;
  // Which specialist agent produced this turn and what it returned (see
  // lib/types.ts). Undefined for user turns and for anything answered
  // before the agent orchestrator existed -- CopilotAgentPanels falls back
  // to plain markdown in that case.
  agent_used?: AgentName | string;
  raw_data?: AgentRawData | null;
}

export interface ConversationSummary {
  id: string;
  title: string;
  category: string | null;
  created_at: string;
  updated_at: string;
}

export interface Conversation extends ConversationSummary {
  messages: StoredMessage[];
}

const CLIENT_ID_KEY = "cortex-copilot-client-id";
const MAX_TITLE_LENGTH = 60;

function isBrowser() {
  return typeof window !== "undefined";
}

/** Returns this browser's sync code, generating and persisting one on
 * first use. Safe to call from anywhere -- it's the identity every
 * request in this file is scoped by. */
export function getClientId(): string {
  if (!isBrowser()) return "";
  let id = window.localStorage.getItem(CLIENT_ID_KEY);
  if (!id) {
    id = crypto.randomUUID();
    window.localStorage.setItem(CLIENT_ID_KEY, id);
  }
  return id;
}

/** Adopts a sync code copied from another browser, so this browser starts
 * seeing that browser's conversation history on the next fetch. */
export function setClientId(id: string): void {
  if (!isBrowser()) return;
  const trimmed = id.trim();
  if (trimmed) window.localStorage.setItem(CLIENT_ID_KEY, trimmed);
}

export function titleFromMessage(text: string): string {
  const clean = text.trim().replace(/\s+/g, " ");
  if (clean.length <= MAX_TITLE_LENGTH) return clean || "New conversation";
  return `${clean.slice(0, MAX_TITLE_LENGTH).trimEnd()}…`;
}

class ConversationApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/conversations${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Client-Id": getClientId(),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ConversationApiError(data.detail || `Conversation request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export async function listConversations(): Promise<ConversationSummary[]> {
  return request<ConversationSummary[]>("");
}

export async function fetchConversation(id: string): Promise<Conversation> {
  return request<Conversation>(`/${id}`);
}

export async function createConversation(category: string | null): Promise<Conversation> {
  return request<Conversation>("", {
    method: "POST",
    body: JSON.stringify({ title: "New conversation", category }),
  });
}

export async function replaceConversation(
  id: string,
  title: string,
  category: string | null,
  messages: StoredMessage[]
): Promise<Conversation> {
  return request<Conversation>(`/${id}`, {
    method: "PUT",
    body: JSON.stringify({ title, category, messages }),
  });
}

export async function deleteConversation(id: string): Promise<void> {
  return request<void>(`/${id}`, { method: "DELETE" });
}
