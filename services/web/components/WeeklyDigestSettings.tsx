"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, CalendarClock, CheckCircle2, Eye, EyeOff, Save, Send, Sparkles } from "lucide-react";
import { useCurrentUser } from "@/lib/useCurrentUser";

type Digest = {
  enabled: boolean;
  weekday: number;
  hour_utc: number;
  recipient_email: string;
  smtp_configured: boolean;
  ai_summary_available: boolean;
  last_sent_at: string | null;
  next_send_at: string | null;
};

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const HOURS = Array.from({ length: 24 }, (_, h) => h);

const fieldStyle = { border: "1px solid var(--border)", background: "var(--canvas)", color: "var(--text)" } as const;

function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** Roadmap 4.5 -- schedule + preview for the weekly digest email. Sits under
 * the alert-notification settings because it shares that panel's recipient
 * and SMTP configuration. */
export default function WeeklyDigestSettings() {
  const { user } = useCurrentUser();
  const isAdmin = user?.role === "admin";

  const [digest, setDigest] = useState<Digest | null>(null);
  const [enabled, setEnabled] = useState(true);
  const [weekday, setWeekday] = useState(0);
  const [hour, setHour] = useState(8);
  const [busy, setBusy] = useState<null | "save" | "send" | "preview">(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewHtml, setPreviewHtml] = useState<string | null>(null);
  const [previewWithAi, setPreviewWithAi] = useState(false);

  const apply = (data: Digest) => {
    setDigest(data);
    setEnabled(data.enabled);
    setWeekday(data.weekday);
    setHour(data.hour_utc);
  };

  useEffect(() => {
    fetch("/api/settings/weekly-digest")
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) throw new Error(data?.detail || "Unable to load digest settings.");
        apply(data);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Unable to load digest settings."));
  }, []);

  const dirty = digest !== null && (enabled !== digest.enabled || weekday !== digest.weekday || hour !== digest.hour_utc);

  const save = async () => {
    setBusy("save"); setMessage(null); setError(null);
    try {
      const res = await fetch("/api/settings/weekly-digest", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ enabled, weekday, hour_utc: hour }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "Unable to save digest settings.");
      apply(data);
      setMessage("Weekly digest schedule saved.");
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to save digest settings."); }
    finally { setBusy(null); }
  };

  const sendNow = async () => {
    setBusy("send"); setMessage(null); setError(null);
    try {
      const res = await fetch("/api/settings/weekly-digest/send", { method: "POST" });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.detail || "Unable to send the digest.");
      setMessage(`Digest sent to ${digest?.recipient_email}. The scheduled send is unaffected.`);
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to send the digest."); }
    finally { setBusy(null); }
  };

  const togglePreview = async () => {
    if (previewHtml) { setPreviewHtml(null); return; }
    setBusy("preview"); setMessage(null); setError(null);
    try {
      const res = await fetch(`/api/settings/weekly-digest/preview?use_ai=${previewWithAi ? "true" : "false"}`);
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail || "Unable to build the preview.");
      }
      setPreviewHtml(await res.text());
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to build the preview."); }
    finally { setBusy(null); }
  };

  return (
    <section className="panel p-5">
      <div className="flex items-start gap-3">
        <CalendarClock className="mt-0.5 h-5 w-5" style={{ color: "var(--accent)" }} />
        <div>
          <h2 className="font-semibold text-color-text">Weekly digest</h2>
          <p className="mt-1 text-sm text-text-faint">
            A summary of the week&apos;s capacity trend, security posture and what the agents caught, sent to{" "}
            <span className="font-medium text-color-text">{digest?.recipient_email ?? "the alert recipient"}</span>.
          </p>
        </div>
      </div>

      <label className="mt-5 flex cursor-pointer items-center gap-3 text-sm text-color-text">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} disabled={!isAdmin} className="h-4 w-4" />
        Send the weekly digest automatically
      </label>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="block text-sm font-medium text-color-text">
          Day
          <select value={weekday} onChange={(e) => setWeekday(Number(e.target.value))} disabled={!isAdmin || !enabled}
            className="mt-2 w-full rounded-[var(--radius-control)] px-3 py-2.5 text-sm outline-none disabled:opacity-60" style={fieldStyle}>
            {WEEKDAYS.map((name, i) => <option key={name} value={i}>{name}</option>)}
          </select>
        </label>
        <label className="block text-sm font-medium text-color-text">
          Time (UTC)
          <select value={hour} onChange={(e) => setHour(Number(e.target.value))} disabled={!isAdmin || !enabled}
            className="mt-2 w-full rounded-[var(--radius-control)] px-3 py-2.5 text-sm outline-none disabled:opacity-60" style={fieldStyle}>
            {HOURS.map((h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
          </select>
        </label>
      </div>

      {digest && (
        <dl className="mt-4 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
          <div className="flex gap-2"><dt className="text-text-faint">Next send</dt><dd className="font-medium text-color-text">{digest.enabled ? formatWhen(digest.next_send_at) : "Paused"}</dd></div>
          <div className="flex gap-2"><dt className="text-text-faint">Last sent</dt><dd className="font-medium text-color-text">{formatWhen(digest.last_sent_at)}</dd></div>
        </dl>
      )}

      <div className="mt-4 flex items-center gap-2 text-sm" style={{ color: digest?.ai_summary_available ? "var(--ok)" : "var(--text-faint)" }}>
        <Sparkles className="h-4 w-4" />
        {digest?.ai_summary_available
          ? "AI summary on — written by NVIDIA NIM from the week's data."
          : "AI summary off (no NVIDIA_API_KEY) — a data-driven summary is used instead."}
      </div>

      {digest && !digest.smtp_configured && (
        <div className="mt-4 flex gap-2 rounded-[var(--radius-control)] p-3 text-sm" style={{ color: "var(--warn)", background: "var(--warn-soft)" }}>
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />SMTP is not configured yet, so the digest can be previewed but not delivered.
        </div>
      )}
      {!isAdmin && user && <p className="mt-4 text-sm text-text-faint">Only admins can change the schedule or send the digest.</p>}
      {message && <p className="mt-4 flex items-center gap-1.5 text-sm" style={{ color: "var(--ok)" }}><CheckCircle2 className="h-4 w-4" />{message}</p>}
      {error && <p className="mt-4 text-sm" style={{ color: "var(--crit)" }}>{error}</p>}

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <button onClick={save} disabled={busy !== null || !isAdmin || !dirty}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-semibold text-white disabled:opacity-50" style={{ background: "var(--accent)" }}>
          <Save className="h-3.5 w-3.5" />{busy === "save" ? "Saving…" : "Save schedule"}
        </button>
        <button onClick={sendNow} disabled={busy !== null || !isAdmin || !digest?.smtp_configured}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-semibold disabled:opacity-50" style={{ border: "1px solid var(--border)", color: "var(--text)" }}>
          <Send className="h-3.5 w-3.5" />{busy === "send" ? "Building & sending…" : "Send now"}
        </button>
        <button onClick={togglePreview} disabled={busy !== null && busy !== "preview"}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-semibold disabled:opacity-50" style={{ border: "1px solid var(--border)", color: "var(--text)" }}>
          {previewHtml ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
          {busy === "preview" ? "Building preview…" : previewHtml ? "Hide preview" : "Preview"}
        </button>
        {!previewHtml && digest?.ai_summary_available && (
          <label className="ml-1 flex cursor-pointer items-center gap-2 text-sm text-text-faint">
            <input type="checkbox" checked={previewWithAi} onChange={(e) => setPreviewWithAi(e.target.checked)} className="h-4 w-4" />
            include AI summary (slower)
          </label>
        )}
      </div>

      {previewHtml && (
        <div className="mt-5 overflow-hidden rounded-[var(--radius-panel)]" style={{ border: "1px solid var(--border)" }}>
          <iframe title="Weekly digest preview" srcDoc={previewHtml} sandbox="" className="block w-full" style={{ height: 760, background: "#F5F6F8" }} />
        </div>
      )}
    </section>
  );
}
