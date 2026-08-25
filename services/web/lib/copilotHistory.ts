import type { AgentName, AgentRawData, ChatMessage, ChatSource } from "@/lib/types";

// -- Copilot conversation history -------------------------------------------
//
// Persisted server-side (see services/api/app/routers/conversations.py),
// scoped by the logged-in account rather than this browser -- the request
// layer just needs to send the normal session cookie (already handled by
// every app/api/ route via lib/serverAuth's authHeaders), so history shows
// up the same way on any device that account logs into.

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

const MAX_TITLE_LENGTH = 60;

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
