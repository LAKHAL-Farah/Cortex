"use client";

import { useState } from "react";
import useSWR from "swr";
import { CheckCircle2, ChevronDown, Clock, Info, Loader2, Play, ScrollText, XCircle } from "lucide-react";
import type {
  RemediationAuditEntry,
  RemediationDecisionKind,
  RemediationEvent,
  RemediationProposalRecord,
  RemediationStatus,
} from "@/lib/types";

/**
 * Roadmap 4.3 -- approve / reject / ask for more information on a proposed
 * fix, with the decision history underneath. Roadmap 4.4 -- an approved fix
 * runs only after a second, explicit click (Confidence Ladder Level 0).
 *
 * Everything shown here is read from the API, never from the chat message:
 * the message only holds the proposal as it was when the turn ran, while the
 * status may have moved since (another admin decided, a reload, another tab).
 * The component also does not decide who may approve -- the API says so
 * through `can_decide` and enforces it on the POST regardless of what this
 * file renders.
 *
 * Approving records a decision. It does not run anything; the copy says so,
 * because "Approved" next to a command is easy to read as "done". Running is a
 * separate button, shown only when the API says `can_execute`, behind a
 * confirmation that names exactly what will happen. The click carries only the
 * proposal's digest -- the API runs what it stored at approval, never what the
 * browser sends -- and re-checks the role, the approval and the impact itself.
 *
 * The card polls while a run or an answer is in flight, so "Executing..." and
 * the answer to a question arrive without a reload.
 */

const STATUS_UI: Record<RemediationStatus, { label: string; color: string; soft: string }> = {
  proposed: { label: "Awaiting decision", color: "var(--text-muted)", soft: "var(--canvas)" },
  info_requested: { label: "More info requested", color: "var(--warning, #b7791f)", soft: "var(--warning-soft, rgba(183,121,31,0.12))" },
  approved: { label: "Approved", color: "var(--success, #2f855a)", soft: "var(--success-soft, rgba(47,133,90,0.12))" },
  rejected: { label: "Rejected", color: "var(--danger, #c53030)", soft: "var(--danger-soft, rgba(197,48,48,0.12))" },
  executing: { label: "Executing...", color: "var(--warning, #b7791f)", soft: "var(--warning-soft, rgba(183,121,31,0.12))" },
  executed: { label: "Executed", color: "var(--success, #2f855a)", soft: "var(--success-soft, rgba(47,133,90,0.12))" },
  execution_failed: { label: "Execution failed", color: "var(--danger, #c53030)", soft: "var(--danger-soft, rgba(197,48,48,0.12))" },
};

// History rows: the statuses plus the two events that are not statuses.
const EVENT_UI: Record<RemediationEvent, { label: string; color: string }> = {
  proposed: { label: "Proposed", color: STATUS_UI.proposed.color },
  info_requested: { label: "Asked for more info", color: STATUS_UI.info_requested.color },
  info_provided: { label: "Cortex answered", color: "var(--accent)" },
  approved: { label: "Approved", color: STATUS_UI.approved.color },
  rejected: { label: "Rejected", color: STATUS_UI.rejected.color },
  executing: { label: "Executing", color: STATUS_UI.executing.color },
  execution_started: { label: "Execution started", color: STATUS_UI.executing.color },
  executed: { label: "Executed", color: STATUS_UI.executed.color },
  execution_failed: { label: "Execution failed", color: STATUS_UI.execution_failed.color },
};

const OPEN: ReadonlyArray<RemediationStatus> = ["proposed", "info_requested"];

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
  const ui = EVENT_UI[entry.event];
  return (
    <li className="flex flex-col gap-0.5 border-l-2 pl-2.5" style={{ borderColor: ui.color }}>
      <div className="flex flex-wrap items-baseline gap-x-2 text-[12px]">
        <span className="font-semibold" style={{ color: ui.color }}>
          {ui.label}
        </span>
        <span className="text-text-muted">
          {entry.actor_username ?? "Cortex"}
          {entry.actor_role ? ` (${entry.actor_role})` : ""}
        </span>
        <time className="text-text-faint" dateTime={entry.created_at}>
          {formatWhen(entry.created_at)}
        </time>
      </div>
      {entry.comment && <p className="whitespace-pre-line text-[12px] leading-relaxed text-text-muted">{entry.comment}</p>}
    </li>
  );
}

export function RemediationApproval({ approvalId }: { approvalId: string }) {
  const { data, error, isLoading, mutate } = useSWR(`/api/remediation/proposals/${approvalId}`, fetcher, {
    revalidateOnFocus: true,
    // A run or an unanswered question is in flight: look again until it settles.
    refreshInterval: (latest) => (latest && (latest.status === "executing" || latest.answer_pending) ? 1500 : 0),
  });
  const [mode, setMode] = useState<RemediationDecisionKind | null>(null);
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [confirmingExecute, setConfirmingExecute] = useState(false);
  const [oneClickStep, setOneClickStep] = useState<"approving" | "starting" | null>(null);

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
  const isOpen = OPEN.includes(data.status);
  const decidedBy = [...data.history].reverse().find((e) => e.event === "approved" || e.event === "rejected");
  const lastAnswer = [...data.history].reverse().find((e) => e.event === "info_provided");
  const lastFailure = [...data.history].reverse().find((e) => e.event === "execution_failed");
  const canRetry = data.status === "execution_failed" && data.can_execute;
  // What the backend actually reported (stored on the audit entry; the API has
  // always returned it, the card just never showed it).
  const failureExec = (lastFailure?.details as { execution?: { error?: string | null; output_tail?: string; operation?: string; backend?: string } } | undefined)
    ?.execution;
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

  const execute = async () => {
    if (submitting) return;
    setSubmitting(true);
    setProblem(null);
    try {
      const res = await fetch(`/api/remediation/proposals/${approvalId}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, proposal_digest: data.proposal_digest }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        void mutate(); // the reason it was refused may be a change the card has not seen yet
        throw new Error(
          typeof body?.detail === "string" ? body.detail : `Execution was not started (${res.status}).`,
        );
      }
      await mutate(body.proposal as RemediationProposalRecord, { revalidate: false });
      setConfirmingExecute(false);
      setShowHistory(true);
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "Execution was not started.");
      setConfirmingExecute(false);
    } finally {
      setSubmitting(false);
    }
  };

  // Sandbox only (the API sets `execution.one_click`). Two separate requests,
  // exactly what clicking Approve and then Execute would send, so the audit
  // trail, the digest check and every server-side rule are unchanged.
  const approveAndExecute = async () => {
    if (submitting) return;
    setSubmitting(true);
    setProblem(null);
    try {
      setOneClickStep("approving");
      const dec = await fetch(`/api/remediation/proposals/${approvalId}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: "approve", comment: "Approved and executed in one step (sandbox one-click)." }),
      });
      const decBody = await dec.json().catch(() => null);
      if (!dec.ok) {
        if (dec.status === 409) void mutate();
        throw new Error(typeof decBody?.detail === "string" ? decBody.detail : `The decision was not recorded (${dec.status}).`);
      }
      const approved = decBody.proposal as RemediationProposalRecord;
      await mutate(approved, { revalidate: false });

      setOneClickStep("starting");
      const run = await fetch(`/api/remediation/proposals/${approvalId}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, proposal_digest: approved.proposal_digest }),
      });
      const runBody = await run.json().catch(() => null);
      if (!run.ok) {
        void mutate();
        throw new Error(
          "Approved, but it did not start: " +
            (typeof runBody?.detail === "string" ? runBody.detail : `Execution was not started (${run.status}).`),
        );
      }
      await mutate(runBody.proposal as RemediationProposalRecord, { revalidate: false });
      setShowHistory(true);
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "Approve & execute did not complete.");
    } finally {
      setOneClickStep(null);
      setSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col gap-2 rounded-[var(--radius-control)] px-3 py-2.5" style={{ background: "var(--canvas)" }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          {data.status === "approved" || data.status === "executed" ? (
            <CheckCircle2 className="h-3.5 w-3.5" style={{ color: status.color }} strokeWidth={2} />
          ) : data.status === "rejected" || data.status === "execution_failed" ? (
            <XCircle className="h-3.5 w-3.5" style={{ color: status.color }} strokeWidth={2} />
          ) : data.status === "executing" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" style={{ color: status.color }} strokeWidth={2} />
          ) : (
            <Clock className="h-3.5 w-3.5" style={{ color: status.color }} strokeWidth={2} />
          )}
          <span className="agent-pill" style={{ color: status.color, background: status.soft }}>
            {status.label}
          </span>
          {!isOpen && decidedBy && (
            <span className="text-[12px] text-text-muted">
              {decidedBy.event === "approved" ? "approved" : "rejected"} by {decidedBy.actor_username ?? "unknown"} ·{" "}
              {formatWhen(decidedBy.created_at)}
            </span>
          )}
        </div>

        {isOpen && mode === null && (
          <div className="flex flex-wrap items-center gap-1.5">
            {data.can_decide && (
              <>
                {data.execution.one_click && (
                  <button
                    type="button"
                    className="agent-pill inline-flex cursor-pointer items-center gap-1.5"
                    onClick={approveAndExecute}
                    disabled={submitting}
                    title="Sandbox only: records the approval, then runs the fix. Both steps are still logged separately."
                    style={{ color: "var(--warning, #b7791f)", background: "var(--warning-soft, rgba(183,121,31,0.12))" }}
                  >
                    {oneClickStep ? <Loader2 className="h-3 w-3 animate-spin" strokeWidth={2} /> : <Play className="h-3 w-3" strokeWidth={2} />}
                    {oneClickStep === "approving" ? "Approving..." : oneClickStep === "starting" ? "Starting..." : "Approve & execute"}
                  </button>
                )}
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

      {data.status === "rejected" && (
        <p className="text-[12px] leading-relaxed text-text-muted">
          Rejection is recorded in the audit trail. Nothing was changed.
        </p>
      )}

      {data.status === "approved" && !confirmingExecute && (
        <div className="flex flex-col gap-1.5">
          <p className="text-[12px] leading-relaxed text-text-muted">
            Approval is recorded in the audit trail. <strong>Nothing has run yet</strong> -- approving only records the decision.
            {data.execution.available
              ? " Cortex can run this fix itself, but only when an admin clicks Execute."
              : " Run the command yourself, as described above."}
          </p>
          {data.execution.blocked_reason && (
            <p className="flex items-start gap-1.5 text-[12px] leading-relaxed text-text-muted">
              <Info className="mt-[1px] h-3.5 w-3.5 shrink-0" strokeWidth={2} />
              <span>Cortex cannot run this one for you: {data.execution.blocked_reason}</span>
            </p>
          )}
          {data.execution.available && !data.can_execute && (
            <p className="text-[12px] leading-relaxed text-text-muted">Only an admin can execute an approved fix.</p>
          )}
        </div>
      )}

      {data.status === "executing" && (
        <p className="text-[12px] leading-relaxed text-text-muted" aria-live="polite">
          Running now{data.execution.summary ? `: ${data.execution.summary}` : ""} This card updates when it finishes.
        </p>
      )}
      {data.status === "executed" && (
        <p className="text-[12px] leading-relaxed text-text-muted" aria-live="polite">
          Executed and recorded in the audit trail. Use the check commands above to confirm the result -- Cortex reports what the
          tool returned, not that the problem is gone.
        </p>
      )}
      {data.status === "execution_failed" && (
        <div className="flex flex-col gap-1" aria-live="polite">
          <p className="text-[12px] leading-relaxed" style={{ color: "var(--danger, #c53030)" }}>
            Execution failed{lastFailure?.comment ? `: ${lastFailure.comment}` : "."}
          </p>
          {failureExec?.error && failureExec.error !== lastFailure?.comment && (
            <p className="break-words font-mono text-[11px] leading-relaxed text-text-muted">Reason: {failureExec.error}</p>
          )}
          {failureExec?.output_tail && (
            <details className="text-[11px] text-text-muted">
              <summary className="cursor-pointer">Output from {failureExec.backend === "ansible" ? "Ansible" : "the backend"}</summary>
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded p-2" style={{ background: "var(--surface, transparent)" }}>
                {failureExec.output_tail}
              </pre>
            </details>
          )}
          <p className="text-[12px] leading-relaxed text-text-muted">
            The attempt is in the audit trail. Check the host before trying again; the state may be partly changed.
          </p>
        </div>
      )}

      {(data.status === "approved" || canRetry) && data.can_execute && !confirmingExecute && mode === null && (
        <div>
          <button
            type="button"
            className="agent-pill inline-flex cursor-pointer items-center gap-1.5"
            onClick={() => {
              setProblem(null);
              setConfirmingExecute(true);
            }}
            style={{ color: "var(--warning, #b7791f)", background: "var(--warning-soft, rgba(183,121,31,0.12))" }}
          >
            <Play className="h-3 w-3" strokeWidth={2} />
            {canRetry ? "Execute again..." : "Execute..."}
          </button>
        </div>
      )}

      {confirmingExecute && (
        <div role="group" aria-label="Confirm execution" className="flex flex-col gap-1.5">
          <p className="text-[12px] font-medium leading-relaxed text-color-text">
            Run this now? {data.execution.summary}
          </p>
          <p className="text-[12px] leading-relaxed text-text-muted">
            This changes your infrastructure
            {data.execution.backend === "ansible" ? " through Ansible" : data.execution.backend === "openstack_sdk" ? " through the OpenStack SDK" : ""}
            . Cortex runs exactly the approved command shown above and records the result in the audit trail.
          </p>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              className="agent-pill cursor-pointer"
              onClick={execute}
              disabled={submitting}
              style={{ color: "var(--warning, #b7791f)", background: "var(--warning-soft, rgba(183,121,31,0.12))" }}
            >
              {submitting ? "Starting..." : "Yes, execute now"}
            </button>
            <button
              type="button"
              className="agent-pill cursor-pointer"
              onClick={() => setConfirmingExecute(false)}
              disabled={submitting}
              style={{ color: "var(--text-muted)", background: "transparent" }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {data.status === "info_requested" && data.answer_pending && (
        <p className="flex items-center gap-2 text-[12px] text-text-muted" aria-live="polite">
          <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} /> Looking your question up in the proposal...
        </p>
      )}
      {data.status === "info_requested" && !data.answer_pending && lastAnswer?.comment && (
        <div className="flex flex-col gap-1 border-l-2 pl-2.5" style={{ borderColor: "var(--accent)" }} aria-live="polite">
          <span className="text-[12px] font-semibold" style={{ color: "var(--accent)" }}>
            Cortex&apos;s answer
          </span>
          <p className="whitespace-pre-line text-[12px] leading-relaxed text-text-muted">{lastAnswer.comment}</p>
          <span className="text-[11px] text-text-faint">
            Taken from the stored proposal only. Still not satisfied? Ask again, or ask a colleague.
          </span>
        </div>
      )}
      {isOpen && !data.can_decide && (
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
