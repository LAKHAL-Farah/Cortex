"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowUp,
  Check,
  Copy,
  FileText,
  Link2,
  MessageSquarePlus,
  MessagesSquare,
  Sparkles,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import type { ChatSource } from "@/lib/types";
import {
  type Conversation,
  type ConversationSummary,
  type StoredMessage,
  createConversation as createRemoteConversation,
  deleteConversation as deleteRemoteConversation,
  fetchConversation,
  getClientId,
  listConversations,
  replaceConversation,
  setClientId,
  titleFromMessage,
} from "@/lib/copilotHistory";

interface DisplayMessage extends StoredMessage {
  pending?: boolean; // true while tokens are still streaming in
}

const SUGGESTIONS = [
  "How is Cinder storage backed?",
  "What runs on the controller node?",
  "Walk me through the network topology.",
  "What's the admin runbook for a failed compute node?",
];

const CATEGORY_OPTIONS: { label: string; value: string | null }[] = [
  { label: "All docs", value: null },
  { label: "Service details", value: "service-detail" },
];

const CITE_SCHEME = "cite:";

// Rewrites the model's inline citation tags ("[nova.md]") into real markdown
// links ("[nova.md](cite:nova.md)") before handing the string to
// react-markdown, so citations render as chips (via the `a` override below)
// without needing a bespoke parser alongside a full markdown renderer.
function withCiteLinks(text: string) {
  return text.replace(/\[([a-zA-Z0-9_.\-/]+\.md)\]/g, `[$1](${CITE_SCHEME}$1)`);
}

function CiteChip({ file }: { file: string }) {
  return (
    <span
      className="mx-0.5 inline-flex items-center gap-1 rounded-[4px] px-1.5 py-[1px] align-middle text-[11px] font-medium no-underline"
      style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
    >
      <FileText className="h-[10px] w-[10px]" strokeWidth={2} />
      {file}
    </span>
  );
}

function Markdown({ text }: { text: string }) {
  return (
    <div className="md-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ href, children }) {
            if (href?.startsWith(CITE_SCHEME)) {
              return <CiteChip file={href.slice(CITE_SCHEME.length)} />;
            }
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {withCiteLinks(text)}
      </ReactMarkdown>
    </div>
  );
}

function SourceChips({ sources }: { sources: ChatSource[] }) {
  if (sources.length === 0) return null;
  return (
    <div className="mt-2.5 flex flex-wrap gap-1.5">
      {sources.map((s, i) => (
        <div
          key={`${s.source_path}-${i}`}
          title={s.heading ?? s.doc_title}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-2 py-1 text-[11px]"
          style={{ border: "1px solid var(--border-soft)", background: "var(--canvas)", color: "var(--text-faint)" }}
        >
          <FileText className="h-[11px] w-[11px]" strokeWidth={1.75} style={{ color: "var(--text-muted)" }} />
          <span className="font-medium text-color-text">{s.doc_title}</span>
          {s.heading && <span className="text-text-muted">· {s.heading}</span>}
        </div>
      ))}
    </div>
  );
}

function TypingDots() {
  return (
    <span className="inline-flex items-center gap-1">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 animate-bounce rounded-full"
          style={{ background: "var(--text-muted)", animationDelay: `${i * 120}ms` }}
        />
      ))}
    </span>
  );
}

function relativeTime(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.round(diff / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  if (day < 7) return `${day}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function SyncCodePanel({ onClose }: { onClose: () => void }) {
  const [code, setCode] = useState(() => getClientId());
  const [pasted, setPasted] = useState("");
  const [copied, setCopied] = useState(false);

  async function copyCode() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard permissions denied -- the code is still selectable text
    }
  }

  function adoptPasted() {
    if (!pasted.trim()) return;
    setClientId(pasted);
    window.location.reload();
  }

  return (
    <div
      className="absolute inset-x-2 bottom-[52px] z-10 rounded-[var(--radius-panel)] p-3 text-[12px]"
      style={{ border: "1px solid var(--border)", background: "var(--surface)", boxShadow: "var(--shadow-hover)" }}
    >
      <div className="mb-1.5 flex items-center justify-between">
        <span className="font-medium text-color-text">Sync across devices</span>
        <button onClick={onClose} className="text-text-muted hover:text-text-dim">
          ✕
        </button>
      </div>
      <p className="mb-2 text-[11.5px] leading-relaxed text-text-faint">
        This code identifies your history. Copy it into Copilot on another device to see the same conversations there.
      </p>
      <div className="mb-2 flex items-center gap-1.5">
        <code
          className="flex-1 truncate rounded-[var(--radius-control)] px-2 py-1 font-mono text-[11px]"
          style={{ background: "var(--canvas)", border: "1px solid var(--border-soft)", color: "var(--text-dim)" }}
        >
          {code}
        </code>
        <button
          onClick={copyCode}
          aria-label="Copy sync code"
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[4px]"
          style={{ background: "var(--canvas)" }}
        >
          {copied ? (
            <Check className="h-3 w-3" style={{ color: "var(--ok)" }} strokeWidth={2} />
          ) : (
            <Copy className="h-3 w-3 text-text-muted" strokeWidth={1.75} />
          )}
        </button>
      </div>
      <div className="flex items-center gap-1.5">
        <input
          value={pasted}
          onChange={(e) => setPasted(e.target.value)}
          placeholder="Paste a code from another device…"
          className="flex-1 rounded-[var(--radius-control)] px-2 py-1 text-[11px] outline-none"
          style={{ background: "var(--canvas)", border: "1px solid var(--border-soft)", color: "var(--text)" }}
        />
        <button
          onClick={adoptPasted}
          disabled={!pasted.trim()}
          className="shrink-0 rounded-[var(--radius-control)] px-2 py-1 text-[11px] font-medium text-white disabled:opacity-40"
          style={{ background: "var(--accent)" }}
        >
          Use
        </button>
      </div>
    </div>
  );
}

function HistoryRail({
  conversations,
  activeId,
  loading,
  onSelect,
  onNew,
  onDelete,
}: {
  conversations: ConversationSummary[];
  activeId: string | null;
  loading: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  const [syncOpen, setSyncOpen] = useState(false);

  return (
    <div
      className="relative hidden w-[196px] shrink-0 flex-col border-r sm:flex"
      style={{ borderColor: "var(--border-soft)" }}
    >
      <div className="p-2.5">
        <button
          onClick={onNew}
          className="panel-interactive flex w-full items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-1.5 text-[12.5px] font-medium text-color-text"
          style={{ border: "1px solid var(--border-soft)", background: "var(--canvas)" }}
        >
          <MessageSquarePlus className="h-[14px] w-[14px]" strokeWidth={1.9} style={{ color: "var(--accent)" }} />
          New chat
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {loading ? (
          <div className="px-1.5 py-3 text-[11.5px] text-text-muted">Loading history…</div>
        ) : conversations.length === 0 ? (
          <div className="px-1.5 py-3 text-[11.5px] leading-relaxed text-text-muted">
            Conversations you start will show up here.
          </div>
        ) : (
          <>
            <div className="eyebrow px-1.5 pb-1 pt-1">History</div>
            <div className="space-y-0.5">
              {conversations.map((c) => {
                const active = c.id === activeId;
                return (
                  <button
                    key={c.id}
                    onClick={() => onSelect(c.id)}
                    className="group relative flex w-full items-start gap-1.5 rounded-[var(--radius-control)] py-1.5 pl-2 pr-1.5 text-left transition-colors"
                    style={{ background: active ? "var(--accent-soft)" : "transparent" }}
                  >
                    <MessagesSquare
                      className="mt-[2px] h-[13px] w-[13px] shrink-0"
                      strokeWidth={1.75}
                      style={{ color: active ? "var(--accent)" : "var(--text-muted)" }}
                    />
                    <span className="min-w-0 flex-1">
                      <span
                        className="block truncate text-[12.5px] leading-tight"
                        style={{ color: active ? "var(--text)" : "var(--text-dim)", fontWeight: active ? 500 : 400 }}
                      >
                        {c.title}
                      </span>
                      <span className="block text-[10.5px] text-text-muted">{relativeTime(c.updated_at)}</span>
                    </span>
                    <span
                      role="button"
                      aria-label="Delete conversation"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete(c.id);
                      }}
                      className="mt-[2px] shrink-0 rounded-[4px] p-1 opacity-0 transition-opacity hover:!opacity-100 group-hover:opacity-60"
                    >
                      <Trash2 className="h-[12px] w-[12px]" strokeWidth={1.75} style={{ color: "var(--text-muted)" }} />
                    </span>
                  </button>
                );
              })}
            </div>
          </>
        )}
      </div>

      <div className="border-t p-2" style={{ borderColor: "var(--border-soft)" }}>
        <button
          onClick={() => setSyncOpen((v) => !v)}
          className="flex w-full items-center gap-2 rounded-[var(--radius-control)] px-2 py-1.5 text-[11.5px] text-text-faint hover:text-text-dim"
        >
          <Link2 className="h-[12px] w-[12px]" strokeWidth={1.75} />
          Sync across devices
        </button>
      </div>
      {syncOpen && <SyncCodePanel onClose={() => setSyncOpen(false)} />}
    </div>
  );
}

export default function CopilotChat() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Load this browser's history on mount. A failure here (backend
  // unreachable, etc.) degrades to an empty, unsaved chat rather than
  // blocking the composer -- history is a convenience, not a dependency of
  // being able to talk to Copilot at all.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await listConversations();
        if (cancelled) return;
        setConversations(list);
      } catch {
        // stay with an empty list; the chat itself still works
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  function upsertSummary(next: ConversationSummary) {
    setConversations((prev) => {
      const others = prev.filter((c) => c.id !== next.id);
      return [next, ...others].sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
    });
  }

  async function persist(id: string, nextMessages: DisplayMessage[]) {
    const clean: StoredMessage[] = nextMessages.map(({ pending, ...m }) => m);
    const firstUser = clean.find((m) => m.role === "user")?.content;
    const existingTitle = conversations.find((c) => c.id === id)?.title;
    const title = existingTitle && existingTitle !== "New conversation" ? existingTitle : titleFromMessage(firstUser ?? "New conversation");
    try {
      const saved = await replaceConversation(id, title, category, clean);
      upsertSummary({
        id: saved.id,
        title: saved.title,
        category: saved.category,
        created_at: saved.created_at,
        updated_at: saved.updated_at,
      });
    } catch {
      // best-effort -- the visible transcript is already correct even if
      // this particular save didn't make it to the server
    }
  }

  function startNewConversation() {
    setActiveId(null);
    setMessages([]);
    setInput("");
    textareaRef.current?.focus();
  }

  async function selectConversation(id: string) {
    if (id === activeId) return;
    try {
      const full = await fetchConversation(id);
      setActiveId(full.id);
      setMessages(full.messages);
      setCategory(full.category);
    } catch {
      // leave the current view as-is if the fetch fails
    }
  }

  async function removeConversation(id: string) {
    setConversations((prev) => prev.filter((c) => c.id !== id));
    if (id === activeId) startNewConversation();
    try {
      await deleteRemoteConversation(id);
    } catch {
      // the row is already gone from the visible list; a stray server-side
      // row isn't worth surfacing an error for
    }
  }

  async function send(question: string) {
    const text = question.trim();
    if (!text || busy) return;

    let conversationId = activeId;
    if (!conversationId) {
      try {
        const created = await createRemoteConversation(category);
        conversationId = created.id;
        setActiveId(created.id);
        upsertSummary({
          id: created.id,
          title: created.title,
          category: created.category,
          created_at: created.created_at,
          updated_at: created.updated_at,
        });
      } catch {
        // no persistence available right now -- fall back to an in-memory
        // thread for this turn rather than blocking the send
        conversationId = null;
      }
    }

    const history = messages.filter((m) => !m.pending && !m.errored).map(({ role, content }) => ({ role, content }));
    const userMessage: DisplayMessage = { role: "user", content: text };
    const assistantMessage: DisplayMessage = { role: "assistant", content: "", sources: [], pending: true };
    const withUser = [...messages, userMessage, assistantMessage];
    setMessages(withUser);
    setInput("");
    setBusy(true);

    try {
      const res = await fetch("/api/knowledge/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history, category }),
      });

      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `Request failed (${res.status})`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let latest = withUser;

      const apply = (fn: (m: DisplayMessage) => DisplayMessage) => {
        latest = updateLast(latest, fn);
        setMessages(latest);
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";

        for (const raw of events) {
          const eventLine = raw.split("\n").find((l) => l.startsWith("event: "));
          const dataLine = raw.split("\n").find((l) => l.startsWith("data: "));
          if (!eventLine || !dataLine) continue;
          const eventName = eventLine.slice("event: ".length).trim();
          const data = JSON.parse(dataLine.slice("data: ".length));

          if (eventName === "sources") {
            apply((m) => ({ ...m, sources: data.sources }));
          } else if (eventName === "token") {
            apply((m) => ({ ...m, content: m.content + data.text }));
          } else if (eventName === "error") {
            apply((m) => ({ ...m, content: data.message, errored: true, pending: false }));
          } else if (eventName === "done") {
            apply((m) => ({ ...m, pending: false }));
          }
        }
      }

      if (conversationId) await persist(conversationId, latest);
    } catch (err) {
      const next = updateLast(withUser, (m) => ({
        ...m,
        content: err instanceof Error ? err.message : "Something went wrong.",
        errored: true,
        pending: false,
      }));
      setMessages(next);
      if (conversationId) await persist(conversationId, next);
    } finally {
      setBusy(false);
      textareaRef.current?.focus();
    }
  }

  function updateLast(list: DisplayMessage[], fn: (m: DisplayMessage) => DisplayMessage): DisplayMessage[] {
    if (list.length === 0) return list;
    const next = [...list];
    next[next.length - 1] = fn(next[next.length - 1]);
    return next;
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  const orderedConversations = useMemo(
    () => [...conversations].sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()),
    [conversations]
  );

  return (
    <div className="copilot-ambient panel flex h-[calc(100vh-2rem-88px)] overflow-hidden">
      <HistoryRail
        conversations={orderedConversations}
        activeId={activeId}
        loading={historyLoading}
        onSelect={selectConversation}
        onNew={startNewConversation}
        onDelete={removeConversation}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* header */}
        <div className="flex items-center justify-between gap-3 border-b p-4" style={{ borderColor: "var(--border-soft)" }}>
          <div className="flex items-center gap-2.5">
            <div
              className="flex h-8 w-8 items-center justify-center rounded-[7px]"
              style={{ background: "var(--accent-soft)" }}
            >
              <Sparkles className="h-4 w-4" style={{ color: "var(--accent)" }} strokeWidth={1.75} />
            </div>
            <div>
              <div className="font-display text-[14px] font-semibold text-color-text">Cortex Copilot</div>
              <div className="text-[12px] text-text-faint">Answers are grounded in docs/knowledge/ and cite their source</div>
            </div>
          </div>

          <div className="flex items-center gap-1 rounded-[var(--radius-control)] p-0.5" style={{ background: "var(--canvas)" }}>
            {CATEGORY_OPTIONS.map((opt) => (
              <button
                key={opt.label}
                onClick={() => setCategory(opt.value)}
                className="rounded-[5px] px-2.5 py-1 text-[12px] font-medium transition-colors"
                style={{
                  background: category === opt.value ? "var(--surface)" : "transparent",
                  color: category === opt.value ? "var(--text)" : "var(--text-faint)",
                  boxShadow: category === opt.value ? "var(--shadow)" : "none",
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* transcript */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-4">
          {messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
              <div className="copilot-orb" aria-hidden />
              <div>
                <div className="font-display text-[15px] font-semibold text-color-text">Ask about your infrastructure</div>
                <div className="mt-1 max-w-sm text-[13px] text-text-faint">
                  Answers cite the docs/knowledge/ file they came from. If it is not in the docs, Copilot says so instead of guessing.
                </div>
              </div>
              <div className="mt-1 grid max-w-md grid-cols-1 gap-2 sm:grid-cols-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="panel-interactive rounded-[var(--radius-control)] px-3 py-2 text-left text-[12.5px] text-text-dim"
                    style={{ border: "1px solid var(--border-soft)" }}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="mx-auto flex max-w-[720px] flex-col gap-5">
              <AnimatePresence initial={false}>
                {messages.map((m, i) =>
                  m.role === "user" ? (
                    <motion.div
                      key={i}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.18 }}
                      className="flex justify-end"
                    >
                      <div
                        className="max-w-[80%] rounded-[var(--radius-panel)] px-3.5 py-2 text-[13.5px]"
                        style={{ background: "var(--accent-soft)", color: "var(--text)" }}
                      >
                        {m.content}
                      </div>
                    </motion.div>
                  ) : (
                    <motion.div
                      key={i}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.18 }}
                      className="flex gap-2.5"
                    >
                      <div
                        className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full"
                        style={{ background: m.errored ? "var(--crit-soft)" : "var(--accent-soft)" }}
                      >
                        {m.errored ? (
                          <TriangleAlert className="h-3 w-3" style={{ color: "var(--crit)" }} strokeWidth={2} />
                        ) : (
                          <Sparkles className="h-3 w-3" style={{ color: "var(--accent)" }} strokeWidth={1.75} />
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        {m.content ? (
                          m.errored ? (
                            <div className="text-[13.5px] leading-relaxed" style={{ color: "var(--crit)" }}>
                              {m.content}
                            </div>
                          ) : (
                            <Markdown text={m.content} />
                          )
                        ) : (
                          <TypingDots />
                        )}
                        {!m.errored && m.sources && <SourceChips sources={m.sources} />}
                      </div>
                    </motion.div>
                  )
                )}
              </AnimatePresence>
            </div>
          )}
        </div>

        {/* composer */}
        <div className="border-t p-3" style={{ borderColor: "var(--border-soft)" }}>
          <div
            className="mx-auto flex max-w-[720px] items-end gap-2 rounded-[var(--radius-panel)] p-2"
            style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
          >
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Ask about nodes, services, networks, or runbooks…"
              rows={1}
              className="max-h-32 flex-1 resize-none bg-transparent px-2 py-1.5 text-[13.5px] text-color-text outline-none placeholder:text-text-muted"
            />
            <button
              onClick={() => send(input)}
              disabled={busy || !input.trim()}
              aria-label="Send"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-control)] text-white transition-opacity disabled:opacity-40"
              style={{ background: "var(--accent)" }}
            >
              <ArrowUp className="h-4 w-4" strokeWidth={2.25} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
