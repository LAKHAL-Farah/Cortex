"use client";

import { useState } from "react";
import useSWR from "swr";
import { CheckCircle2, ChevronDown, Clock, Info, Loader2, ScrollText, XCircle } from "lucide-react";
import type {
  RemediationAuditEntry,
  RemediationDecisionKind,
  RemediationProposalRecord,
  RemediationStatus,
} from "@/lib/types";

/**
 * Roadmap 4.3 -- approve / reject / ask for more information on a proposed
 * fix, with the decision history underneath.
 *
 * Everything shown here is read from the API, never from the chat message:
 * the message only holds the proposal as it was when the turn ran, while the
 * status may have moved since (another admin decided, a reload, another tab).
 * The component also does not decide who may approve -- the API says so
 * through `can_decide` and enforces it on the POST regardless of what this
 * file renders.
 *
 * Approving records a decision. It does not run anything; the copy says so,
 * because "Approved" next to a command is easy to read as "done".
 */

const STATUS_UI: Record<RemediationStatus, { label: string; color: string; soft: string }> = {
  proposed: { label: "Awaiting decision", color: "var(--text-muted)", soft: "var(--canvas)" },
  info_requested: { label: "More info requested", color: "var(--warning, #b7791f)", soft: "var(--warning-soft, rgba(183,121,31,0.12))" },
  approved: { label: "Approved", color: "var(--success, #2f855a)", soft: "var(--success-soft, rgba(47,133,90,0.12))" },
  rejected: { label: "Rejected", color: "var(--danger, #c53030)", soft: "var(--danger-soft, rgba(197,48,48,0.12))" },
};

const EVENT_LABEL: Record<RemediationStatus, string> = {
  proposed: "Proposed",
  info_requested: "Asked for more info",
  approved: "Approved",
  rejected: "Rejected",
};

const MODE_COPY: Record<
  RemediationDecisionKind,
  { prompt: string; submit: string; placeholder: string }
> = {
  approve: {
    prompt: "Approve this fix? Nothing is run -- this only records your decision.",
    submit: "Confirm approval",
    placeholder: "Note (optional unless the fix is high risk)",
  },
  reject: {
    prompt: "Why are you rejecting this fix?",
    submit: "Confirm rejection",
    placeholder: "Reason (required)",
  },
  ask_more_info: {
    prompt: "What do you need to know before this can be decided?",
    submit: "Send question",
    placeholder: "Your question (required)",
  },
};

const fetcher = async (url: string): Promise<RemediationProposalRecord> => {
  const res = await fetch(url, { cache: "no-store" });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error((data && (data.detail || data.message)) || `Request failed (${res.status})`);
  return data as RemediationProposalRecord;
};

function formatWhen(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function HistoryRow({ entry }: { entry: RemediationAuditEntry }) {
  const ui = STATUS_UI[entry.event];
  return (
    <li className="flex flex-col gap-0.5 border-l-2 pl-2.5" style={{ borderColor: ui.color }}>
      <div className="flex flex-wrap items-baseline gap-x-2 text-[12px]">
        <span className="font-semibold" style={{ color: ui.color }}>
          {EVENT_LABEL[entry.event]}
        </span>
        <span className="text-text-muted">
          {entry.actor_username ?? "system"}
          {entry.actor_role ? ` (${entry.actor_role})` : ""}
        </span>
        <time className="text-text-faint" dateTime={entry.created_at}>
          {formatWhen(entry.created_at)}
        </time>
      </div>
      {entry.comment && <p className="text-[12px] leading-relaxed text-text-muted">{entry.comment}</p>}
    </li>
  );
}

export function RemediationApproval({ approvalId }: { approvalId: string }) {
  const { data, error, isLoading, mutate } = useSWR(`/api/remediation/proposals/${approvalId}`, fetcher, {
    revalidateOnFocus: true,
  });
  const [mode, setMode] = useState<RemediationDecisionKind | null>(null);
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  if (isLoading && !data) {
    return (
      <div className="flex items-center gap-2 text-[12px] text-text-muted">
        <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} /> Checking approval status...
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="flex items-start gap-2 text-[12px] text-text-muted">
        <Info className="mt-[1px] h-3.5 w-3.5 shrink-0" strokeWidth={2} />
        <span>Approval status is unavailable right now{error?.message ? ` (${error.message})` : ""}.</span>
      </div>
    );
  }

  const status = STATUS_UI[data.status];
  const isFinal = data.status === "approved" || data.status === "rejected";
  const decidedBy = [...data.history].reverse().find((e) => e.event === data.status);
  const commentRequired =
    mode === "reject" || mode === "ask_more_info" || (mode === "approve" && data.effective_risk === "high");

  const open = (next: RemediationDecisionKind) => {
    setMode(next);
    setComment("");
    setProblem(null);
  };
  const cancel = () => {
    setMode(null);
    setComment("");
    setProblem(null);
  };

  const submit = async () => {
    if (!mode || submitting) return;
    if (commentRequired && !comment.trim()) {
      setProblem(MODE_COPY[mode].placeholder.replace(/ \(.*\)$/, "") + " is required.");
      return;
    }
    setSubmitting(true);
    setProblem(null);
    try {
      const res = await fetch(`/api/remediation/proposals/${approvalId}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: mode, comment: comment.trim() || undefined }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        // 409 = someone else decided first; refresh so the card shows who.
        if (res.status === 409) void mutate();
        throw new Error(
          typeof body?.detail === "string" ? body.detail : `The decision was not recorded (${res.status}).`,
        );
      }
      await mutate(body.proposal as RemediationProposalRecord, { revalidate: false });
      cancel();
      setShowHistory(true);
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "The decision was not recorded.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col gap-2 rounded-[var(--radius-control)] px-3 py-2.5" style={{ background: "var(--canvas)" }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          {data.status === "approved" ? (
            <CheckCircle2 className="h-3.5 w-3.5" style={{ color: status.color }} strokeWidth={2} />
          ) : data.status === "rejected" ? (
            <XCircle className="h-3.5 w-3.5" style={{ color: status.color }} strokeWidth={2} />
          ) : (
            <Clock className="h-3.5 w-3.5" style={{ color: status.color }} strokeWidth={2} />
          )}
          <span className="agent-pill" style={{ color: status.color, background: status.soft }}>
            {status.label}
          </span>
          {isFinal && decidedBy && (
            <span className="text-[12px] text-text-muted">
              by {decidedBy.actor_username ?? "unknown"} · {formatWhen(decidedBy.created_at)}
            </span>
          )}
        </div>

        {!isFinal && mode === null && (
          <div className="flex flex-wrap items-center gap-1.5">
            {data.can_decide && (
              <>
                <button type="button" className="agent-pill cursor-pointer" onClick={() => open("approve")}
                  style={{ color: "var(--success, #2f855a)", background: "var(--success-soft, rgba(47,133,90,0.12))" }}>
                  Approve
                </button>
                <button type="button" className="agent-pill cursor-pointer" onClick={() => open("reject")}
                  style={{ color: "var(--danger, #c53030)", background: "var(--danger-soft, rgba(197,48,48,0.12))" }}>
                  Reject
                </button>
              </>
            )}
            <button type="button" className="agent-pill cursor-pointer" onClick={() => open("ask_more_info")}
              style={{ color: "var(--text-muted)", background: "var(--surface, transparent)" }}>
              Ask for more info
            </button>
          </div>
        )}
      </div>

      {isFinal && (
        <p className="text-[12px] leading-relaxed text-text-muted">
          {data.status === "approved"
            ? "Approval is recorded in the audit trail. Cortex has not run anything -- run the command yourself, as described above."
            : "Rejection is recorded in the audit trail. Nothing was changed."}
        </p>
      )}
      {!isFinal && !data.can_decide && (
        <p className="text-[12px] leading-relaxed text-text-muted">
          Only an admin can approve or reject. You can ask for more information, and it will be logged.
        </p>
      )}

      {mode && (
        <div className="flex flex-col gap-1.5">
          <label className="text-[12px] font-medium text-color-text" htmlFor={`approval-comment-${approvalId}`}>
            {MODE_COPY[mode].prompt}
          </label>
          <textarea
            id={`approval-comment-${approvalId}`}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            maxLength={2000}
            rows={2}
            placeholder={MODE_COPY[mode].placeholder}
            disabled={submitting}
            className="w-full resize-y rounded-[var(--radius-control)] border px-2 py-1.5 text-[12px] text-color-text"
            style={{ borderColor: "var(--border)", background: "var(--surface, transparent)" }}
          />
          {problem && (
            <p role="alert" className="text-[12px]" style={{ color: "var(--danger, #c53030)" }}>
              {problem}
            </p>
          )}
          <div className="flex items-center gap-1.5">
            <button type="button" className="agent-pill cursor-pointer" onClick={submit} disabled={submitting}
              style={{ color: "var(--accent)", background: "var(--accent-soft)" }}>
              {submitting ? "Saving..." : MODE_COPY[mode].submit}
            </button>
            <button type="button" className="agent-pill cursor-pointer" onClick={cancel} disabled={submitting}
              style={{ color: "var(--text-muted)", background: "transparent" }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {!mode && problem && (
        <p role="alert" className="text-[12px]" style={{ color: "var(--danger, #c53030)" }}>
          {problem}
        </p>
      )}

      <button
        type="button"
        onClick={() => setShowHistory((v) => !v)}
        aria-expanded={showHistory}
        className="flex w-fit cursor-pointer items-center gap-1.5 text-[12px] text-text-muted"
      >
        <ScrollText className="h-3.5 w-3.5" strokeWidth={2} />
        Decision history ({data.history.length})
        <ChevronDown className={`h-3 w-3 transition-transform ${showHistory ? "rotate-180" : ""}`} strokeWidth={2} />
      </button>
      {showHistory && (
        <ul className="flex flex-col gap-2">
          {data.history.map((entry) => (
            <HistoryRow key={entry.id} entry={entry} />
          ))}
        </ul>
      )}
    </div>
  );
}
