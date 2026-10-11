"use client";

import { useEffect, useRef, useState } from "react";
import { Activity, AlertTriangle, CheckCircle2, Save } from "lucide-react";
import { useCurrentUser } from "@/lib/useCurrentUser";
import type {
  DetectionPreview,
  DetectionSettings,
  DetectionValues,
  SensitivityPreset,
} from "@/lib/types";

/** Admin control over how readily Cortex raises an alert. Sits on the Settings
 * page next to the alert-notification settings. Anyone signed in can see the
 * current setting; only admins can change it (the API enforces that too). */

const fieldStyle = { border: "1px solid var(--border)", background: "var(--canvas)", color: "var(--text)" } as const;

const PRESET_COPY: Record<Exclude<SensitivityPreset, "custom">, { title: string; blurb: string }> = {
  low: { title: "Low", blurb: "Fewer alerts. Only larger swings count, and they must last a couple of minutes." },
  balanced: { title: "Balanced", blurb: "Cortex's original behaviour. Alert on the first clearly abnormal reading." },
  high: { title: "High", blurb: "More alerts, sooner. Catches smaller deviations; expect more noise." },
};

type Draft = { z: Record<"medium" | "high" | "critical", string>; ssh: Record<"medium" | "high" | "critical", string>; persist: string };

function toDraft(v: DetectionValues): Draft {
  return {
    z: { medium: String(v.z.medium), high: String(v.z.high), critical: String(v.z.critical) },
    ssh: { medium: String(v.ssh.medium), high: String(v.ssh.high), critical: String(v.ssh.critical) },
    persist: String(v.persist_minutes),
  };
}

function body(preset: SensitivityPreset, draft: Draft) {
  if (preset !== "custom") return { preset };
  return {
    preset,
    custom: {
      z: { medium: Number(draft.z.medium), high: Number(draft.z.high), critical: Number(draft.z.critical) },
      ssh: { medium: Number(draft.ssh.medium), high: Number(draft.ssh.high), critical: Number(draft.ssh.critical) },
      persist_minutes: Number(draft.persist),
    },
  };
}

function summary(v: DetectionValues): string {
  const wait = v.persist_minutes > 0 ? `, must last ~${v.persist_minutes} min` : "";
  return `Alerts at ${v.z.medium} / ${v.z.high} / ${v.z.critical} σ${wait}`;
}

function NumberField({
  label, value, onChange, disabled, step,
}: { label: string; value: string; onChange: (v: string) => void; disabled: boolean; step?: string }) {
  return (
    <label className="block text-xs font-medium text-text-dim">
      {label}
      <input
        type="number"
        inputMode="decimal"
        step={step ?? "any"}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded-[var(--radius-control)] px-2.5 py-2 text-sm outline-none disabled:opacity-60"
        style={fieldStyle}
      />
    </label>
  );
}

export default function AnomalySensitivitySettings() {
  const { user } = useCurrentUser();
  const isAdmin = user?.role === "admin";

  const [settings, setSettings] = useState<DetectionSettings | null>(null);
  const [preset, setPreset] = useState<SensitivityPreset>("balanced");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [preview, setPreview] = useState<DetectionPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  const apply = (data: DetectionSettings) => {
    setSettings(data);
    setPreset(data.preset);
    setDraft(toDraft(data.preset === "custom" ? data.effective : data.custom));
  };

  useEffect(() => {
    fetch("/api/settings/anomaly-detection")
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) throw new Error(data?.detail || "Unable to load detection settings.");
        apply(data);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Unable to load detection settings."));
  }, []);

  // Live "what would this have done?" -- only for admins (the endpoint is admin-only).
  useEffect(() => {
    if (!isAdmin || !settings || !draft) return;
    const mine = ++seq.current;
    const timer = setTimeout(async () => {
      try {
        const res = await fetch("/api/settings/anomaly-detection/preview", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body(preset, draft)),
        });
        const data = await res.json();
        if (mine !== seq.current) return; // a newer request superseded this one
        if (!res.ok) {
          setPreview(null);
          setPreviewError(typeof data?.detail === "string" ? data.detail : "These values are not valid.");
          return;
        }
        setPreviewError(null);
        setPreview(data);
      } catch {
        if (mine === seq.current) setPreviewError("Could not compute the preview.");
      }
    }, 350);
    return () => clearTimeout(timer);
  }, [isAdmin, settings, preset, draft]);

  const save = async () => {
    if (!draft) return;
    setSaving(true); setMessage(null); setError(null);
    try {
      const res = await fetch("/api/settings/anomaly-detection", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body(preset, draft)),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "Unable to save.");
      apply(data);
      setMessage("Sensitivity saved. It applies from the next detection pass.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save.");
    } finally {
      setSaving(false);
    }
  };

  const setZ = (k: "medium" | "high" | "critical", v: string) => setDraft((d) => d && { ...d, z: { ...d.z, [k]: v } });
  const setSsh = (k: "medium" | "high" | "critical", v: string) => setDraft((d) => d && { ...d, ssh: { ...d.ssh, [k]: v } });
  const dirty = settings != null && preset !== settings.preset;
  const editable = isAdmin && preset === "custom";

  return (
    <section className="panel p-5">
      <div className="flex items-start gap-3">
        <Activity className="mt-0.5 h-5 w-5" style={{ color: "var(--accent)" }} />
        <div>
          <h2 className="font-semibold text-color-text">Anomaly detection sensitivity</h2>
          <p className="mt-1 text-sm text-text-faint">
            How far from normal a reading has to be before Cortex raises an alert. Lower sensitivity means fewer, more certain alerts.
            σ is how many &ldquo;normal swings&rdquo; away from that host&apos;s usual level a reading is.
          </p>
        </div>
      </div>

      {!settings && !error && <p className="mt-5 text-sm text-text-faint">Loading…</p>}

      {settings && draft && (
        <>
          <div role="radiogroup" aria-label="Sensitivity" className="mt-5 grid gap-2 md:grid-cols-2">
            {(["low", "balanced", "high"] as const).map((name) => {
              const active = preset === name;
              return (
                <button
                  key={name}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  disabled={!isAdmin}
                  onClick={() => setPreset(name)}
                  className="rounded-[var(--radius-control)] p-3 text-left transition-colors disabled:cursor-default"
                  style={{ border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`, background: active ? "var(--accent-soft)" : "var(--canvas)" }}
                >
                  <div className="flex items-center justify-between text-sm font-semibold text-color-text">
                    {PRESET_COPY[name].title}
                    {settings.preset === name && <span className="text-[11px] font-medium text-text-faint">current</span>}
                  </div>
                  <p className="mt-1 text-xs text-text-dim">{PRESET_COPY[name].blurb}</p>
                  <p className="mt-1.5 text-[11px] text-text-faint">{summary(settings.presets[name])}</p>
                </button>
              );
            })}
            <button
              type="button"
              role="radio"
              aria-checked={preset === "custom"}
              disabled={!isAdmin}
              onClick={() => setPreset("custom")}
              className="rounded-[var(--radius-control)] p-3 text-left transition-colors disabled:cursor-default"
              style={{ border: `1px solid ${preset === "custom" ? "var(--accent)" : "var(--border)"}`, background: preset === "custom" ? "var(--accent-soft)" : "var(--canvas)" }}
            >
              <div className="flex items-center justify-between text-sm font-semibold text-color-text">
                Custom
                {settings.preset === "custom" && <span className="text-[11px] font-medium text-text-faint">current</span>}
              </div>
              <p className="mt-1 text-xs text-text-dim">Set each threshold yourself.</p>
              <p className="mt-1.5 text-[11px] text-text-faint">{summary(settings.custom)}</p>
            </button>
          </div>

          {preset === "custom" && (
            <div className="mt-4 grid gap-4 rounded-[var(--radius-control)] p-4" style={{ border: "1px solid var(--border)" }}>
              <div>
                <div className="text-sm font-medium text-color-text">Deviation needed (σ)</div>
                <div className="mt-2 grid grid-cols-3 gap-3">
                  <NumberField label="Medium" step="0.1" value={draft.z.medium} onChange={(v) => setZ("medium", v)} disabled={!editable} />
                  <NumberField label="High" step="0.1" value={draft.z.high} onChange={(v) => setZ("high", v)} disabled={!editable} />
                  <NumberField label="Critical" step="0.1" value={draft.z.critical} onChange={(v) => setZ("critical", v)} disabled={!editable} />
                </div>
                <p className="mt-1.5 text-xs text-text-faint">Between {settings.limits.z[0]} and {settings.limits.z[1]}, increasing. Higher numbers mean fewer alerts.</p>
              </div>
              <div>
                <div className="text-sm font-medium text-color-text">Failed SSH logins in 5 minutes</div>
                <div className="mt-2 grid grid-cols-3 gap-3">
                  <NumberField label="Medium" step="1" value={draft.ssh.medium} onChange={(v) => setSsh("medium", v)} disabled={!editable} />
                  <NumberField label="High" step="1" value={draft.ssh.high} onChange={(v) => setSsh("high", v)} disabled={!editable} />
                  <NumberField label="Critical" step="1" value={draft.ssh.critical} onChange={(v) => setSsh("critical", v)} disabled={!editable} />
                </div>
                <p className="mt-1.5 text-xs text-text-faint">A fixed count that alerts regardless of what Cortex has learned as normal.</p>
              </div>
              <div className="max-w-[220px]">
                <NumberField
                  label="Must stay abnormal for (minutes)" step="1" value={draft.persist}
                  onChange={(v) => setDraft((d) => d && { ...d, persist: v })} disabled={!editable}
                />
                <p className="mt-1.5 text-xs text-text-faint">0 alerts on the first abnormal reading. Higher ignores short blips.</p>
              </div>
            </div>
          )}

          {isAdmin && (
            <div className="mt-4 rounded-[var(--radius-control)] p-3 text-sm" style={{ background: "var(--canvas)" }}>
              <div className="text-xs font-medium uppercase tracking-wide text-text-faint">Effect on the last {preview?.window_days ?? 7} days</div>
              {previewError ? (
                <p className="mt-1" style={{ color: "var(--warn)" }}>{previewError}</p>
              ) : preview ? (
                <>
                  <p className="mt-1 text-color-text">
                    <strong>{preview.alerts_raised}</strong> alerts were raised. With this setting about{" "}
                    <strong>{preview.alerts_would_raise}</strong> would have been.
                  </p>
                  <p className="mt-1 text-xs text-text-dim">
                    Critical {preview.by_severity_raised.critical} → {preview.by_severity_would_raise.critical} · High{" "}
                    {preview.by_severity_raised.high} → {preview.by_severity_would_raise.high} · Medium{" "}
                    {preview.by_severity_raised.medium} → {preview.by_severity_would_raise.medium}
                    {preview.unaffected_service_alerts > 0 && ` · ${preview.unaffected_service_alerts} service-down alerts are not affected`}
                  </p>
                </>
              ) : (
                <p className="mt-1 text-text-faint">Calculating…</p>
              )}
            </div>
          )}

          <p className="mt-4 text-xs text-text-faint">
            Service-down alerts (a node or container stopping) are facts, not statistics, so they are never filtered by this setting.
            Detection runs every {settings.detection_interval_seconds} seconds.
            {settings.updated_by && settings.updated_at && (
              <> Last changed by {settings.updated_by} on {new Date(settings.updated_at).toLocaleString()}.</>
            )}
          </p>

          {!isAdmin && (
            <p className="mt-3 flex gap-2 text-sm" style={{ color: "var(--warn)" }}>
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />Only an admin can change the sensitivity.
            </p>
          )}
          {message && <p className="mt-4 flex items-center gap-1.5 text-sm" style={{ color: "var(--ok)" }}><CheckCircle2 className="h-4 w-4" />{message}</p>}
          {error && <p className="mt-4 text-sm" style={{ color: "var(--crit)" }}>{error}</p>}

          {isAdmin && (
            <div className="mt-5">
              <button
                onClick={save}
                disabled={saving || !!previewError || (!dirty && preset !== "custom")}
                className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-semibold text-white disabled:opacity-60"
                style={{ background: "var(--accent)" }}
              >
                <Save className="h-3.5 w-3.5" />{saving ? "Saving…" : "Save sensitivity"}
              </button>
            </div>
          )}
        </>
      )}
      {error && !settings && <p className="mt-4 text-sm" style={{ color: "var(--crit)" }}>{error}</p>}
    </section>
  );
}
