"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp, FileText, Sparkles, TriangleAlert } from "lucide-react";
import type { ChatMessage, ChatSource } from "@/lib/types";

interface DisplayMessage extends ChatMessage {
  sources?: ChatSource[];
  pending?: boolean; // true while tokens are still streaming in
  errored?: boolean;
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

// Splits assistant text on citation tags like "[nova.md]" so they can be
// rendered as small chips instead of raw bracket text -- keeps the model's
// own citation format (see chat.py's system prompt) directly renderable
// without pulling in a markdown dependency for one pattern.
function renderWithCitations(text: string) {
  const parts = text.split(/(\[[a-zA-Z0-9_.\-/]+\.md\])/g);
  return parts.map((part, i) => {
    const match = part.match(/^\[([a-zA-Z0-9_.\-/]+\.md)\]$/);
    if (!match) return <span key={i}>{part}</span>;
    return (
      <span
        key={i}
        className="mx-0.5 inline-flex items-center gap-1 rounded-[4px] px-1.5 py-[1px] align-middle text-[11px] font-medium"
        style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
      >
        <FileText className="h-[10px] w-[10px]" strokeWidth={2} />
        {match[1]}
      </span>
    );
  });
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

export default function CopilotChat() {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  async function send(question: string) {
    const text = question.trim();
    if (!text || busy) return;

    const history = messages.filter((m) => !m.pending && !m.errored).map(({ role, content }) => ({ role, content }));
    const userMessage: DisplayMessage = { role: "user", content: text };
    const assistantMessage: DisplayMessage = { role: "assistant", content: "", sources: [], pending: true };
    setMessages((prev) => [...prev, userMessage, assistantMessage]);
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
            setMessages((prev) => updateLast(prev, (m) => ({ ...m, sources: data.sources })));
          } else if (eventName === "token") {
            setMessages((prev) => updateLast(prev, (m) => ({ ...m, content: m.content + data.text })));
          } else if (eventName === "error") {
            setMessages((prev) => updateLast(prev, (m) => ({ ...m, content: data.message, errored: true, pending: false })));
          } else if (eventName === "done") {
            setMessages((prev) => updateLast(prev, (m) => ({ ...m, pending: false })));
          }
        }
      }
    } catch (err) {
      setMessages((prev) =>
        updateLast(prev, (m) => ({
          ...m,
          content: err instanceof Error ? err.message : "Something went wrong.",
          errored: true,
          pending: false,
        }))
      );
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

  return (
    <div className="panel flex h-[calc(100vh-2rem-88px)] flex-col overflow-hidden">
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
            <div
              className="flex h-11 w-11 items-center justify-center rounded-full"
              style={{ background: "var(--accent-soft)" }}
            >
              <Sparkles className="h-5 w-5" style={{ color: "var(--accent)" }} strokeWidth={1.75} />
            </div>
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
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="flex justify-end">
                  <div
                    className="max-w-[80%] rounded-[var(--radius-panel)] px-3.5 py-2 text-[13.5px]"
                    style={{ background: "var(--accent-soft)", color: "var(--text)" }}
                  >
                    {m.content}
                  </div>
                </div>
              ) : (
                <div key={i} className="flex gap-2.5">
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
                      <div
                        className="whitespace-pre-wrap text-[13.5px] leading-relaxed"
                        style={{ color: m.errored ? "var(--crit)" : "var(--text)" }}
                      >
                        {renderWithCitations(m.content)}
                      </div>
                    ) : (
                      <TypingDots />
                    )}
                    {!m.errored && m.sources && <SourceChips sources={m.sources} />}
                  </div>
                </div>
              )
            )}
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
  );
}
