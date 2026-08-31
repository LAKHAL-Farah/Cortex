"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Mail, Save, Send, Settings2, AlertTriangle } from "lucide-react";

type Settings = { recipient_email: string; enabled: boolean; smtp_configured: boolean };

export default function AlertEmailSettings() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [email, setEmail] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch("/api/settings/alert-email")
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) throw new Error(data?.detail || "Unable to load settings.");
        setSettings(data); setEmail(data.recipient_email); setEnabled(data.enabled);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Unable to load settings."));
  }, []);

  const save = async () => {
    setSaving(true); setMessage(null); setError(null);
    try {
      const res = await fetch("/api/settings/alert-email", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ recipient_email: email, enabled }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || "Unable to save settings.");
      setSettings(data); setEmail(data.recipient_email); setMessage("Alert email settings saved.");
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to save settings."); }
    finally { setSaving(false); }
  };

  const sendTest = async () => {
    setSaving(true); setMessage(null); setError(null);
    try {
      const res = await fetch("/api/settings/alert-email/test", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || "Unable to send the test email.");
      setMessage(`Test email sent to ${email}.`);
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to send the test email."); }
    finally { setSaving(false); }
  };

  return (
    <main className="grid max-w-3xl gap-4">
      <div className="glow-surface panel flex items-center gap-3 p-5">
        <div className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-control)]" style={{ background: "var(--canvas)" }}><Settings2 className="h-4.5 w-4.5 text-text-dim" /></div>
        <div><div className="eyebrow">Administration</div><h1 className="font-display mt-1 text-lg font-semibold text-color-text">Alert notifications</h1><p className="mt-0.5 text-sm text-text-faint">Choose where Cortex sends new high and critical alerts.</p></div>
      </div>

      <section className="panel p-5">
        <div className="flex items-start gap-3"><Mail className="mt-0.5 h-5 w-5" style={{ color: "var(--accent)" }} /><div><h2 className="font-semibold text-color-text">Email recipient</h2><p className="mt-1 text-sm text-text-faint">One shared address for now. It can be changed at any time.</p></div></div>
        <label className="mt-5 block text-sm font-medium text-color-text">Recipient email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} className="mt-2 w-full rounded-[var(--radius-control)] px-3 py-2.5 text-sm outline-none" style={{ border: "1px solid var(--border)", background: "var(--canvas)" }} /></label>
        <label className="mt-4 flex cursor-pointer items-center gap-3 text-sm text-color-text"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} className="h-4 w-4" /> Send new high and critical alerts to this address</label>
        {settings && !settings.smtp_configured && <div className="mt-5 flex gap-2 rounded-[var(--radius-control)] p-3 text-sm" style={{ color: "var(--warn)", background: "var(--warn-soft)" }}><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />SMTP is not configured yet. Save the recipient now, then add the team&apos;s SMTP credentials before testing delivery.</div>}
        {message && <p className="mt-4 flex items-center gap-1.5 text-sm" style={{ color: "var(--ok)" }}><CheckCircle2 className="h-4 w-4" />{message}</p>}
        {error && <p className="mt-4 text-sm" style={{ color: "var(--crit)" }}>{error}</p>}
        <div className="mt-6 flex flex-wrap gap-2"><button onClick={save} disabled={saving} className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-semibold text-white disabled:opacity-60" style={{ background: "var(--accent)" }}><Save className="h-3.5 w-3.5" />{saving ? "Saving…" : "Save settings"}</button><button onClick={sendTest} disabled={saving || !settings?.smtp_configured} className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-semibold disabled:opacity-50" style={{ border: "1px solid var(--border)", color: "var(--text)" }}><Send className="h-3.5 w-3.5" />Send test email</button></div>
      </section>
    </main>
  );
}
