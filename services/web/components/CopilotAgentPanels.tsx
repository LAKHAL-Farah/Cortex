"use client";

import { isValidElement, useEffect, useMemo, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock,
  Copy,
  Cpu,
  Crown,
  ExternalLink,
  FileText,
  Gauge,
  Globe,
  HardDrive,
  Info,
  Layers,
  Lightbulb,
  Loader2,
  Lock,
  MemoryStick,
  Minus,
  Network,
  Route,
  Router,
  ScrollText,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Terminal,
  TrendingDown,
  TrendingUp,
  Waypoints,
  Wrench,
  XCircle,
  Zap,
} from "lucide-react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type {
  AgentAnomalyData,
  AgentExpertCommand,
  AgentExpertData,
  IncidentArbitration,
  ParallelismSummary,
  AgentMonitoringData,
  AgentMonitoringFleetData,
  AgentPredictionFleetData,
  AgentName,
  AgentNetworkData,
  AgentPredictionData,
  AgentRagData,
  AgentRawData,
  AgentSecurityData,
  AgentTraceStep,
  ForecastPoint,
  NeutronAgentStatus,
} from "@/lib/types";
import NetworkTopologyDiagram from "@/components/NetworkTopologyDiagram";
import { formatRelativeTime } from "@/lib/logs";
import { metricLabel, SEVERITY_COLOR, SEVERITY_LABEL, SEVERITY_SOFT } from "@/lib/anomalies";

// ---------------------------------------------------------------------------
// Agent identity -- one accent color + icon per specialist (see services/api
// /app/agents/), reused for the reasoning trace, the message accent border,
// and each panel's header so a given agent looks the same wherever it shows
// up in the transcript.
// ---------------------------------------------------------------------------

export const AGENT_META: Record<
  AgentName,
  { label: string; short: string; icon: typeof Activity; color: string; soft: string }
> = {
  monitoring: {
    label: "Monitoring agent",
    short: "Live status",
    icon: Activity,
    color: "var(--ok)",
    soft: "var(--ok-soft)",
  },
  prediction: {
    label: "Prediction agent",
    short: "Forecast",
    icon: TrendingUp,
    color: "var(--medium)",
    soft: "var(--medium-soft)",
  },
  rag: {
    label: "Knowledge agent",
    short: "Docs & runbooks",
    icon: BookOpen,
    color: "var(--chart-2)",
    soft: "rgba(59,126,196,0.12)",
  },
  anomaly: {
    label: "Anomaly agent",
    short: "Incident investigation",
    icon: ShieldAlert,
    color: "var(--crit)",
    soft: "var(--crit-soft)",
  },
  openstack_expert: {
    label: "OpenStack Expert agent",
    short: "Runbook & commands",
    icon: Wrench,
    color: "var(--accent)",
    soft: "var(--accent-soft)",
  },
  network: {
    label: "Network agent",
    short: "Traffic & Neutron health",
    icon: Network,
    color: "var(--chart-6)",
    soft: "rgba(43,158,158,0.12)",
  },
  security: {
    label: "Security agent",
    short: "Auth, CVEs & kernel signals",
    icon: Lock,
    color: "var(--chart-4)",
    soft: "rgba(139,127,224,0.12)",
  },
};

const DEFAULT_META = {
  label: "Copilot",
  short: "",
  icon: Sparkles,
  color: "var(--accent)",
  soft: "var(--accent-soft)",
};

export function agentMeta(agentUsed?: string | null) {
  if (agentUsed && agentUsed in AGENT_META) return AGENT_META[agentUsed as AgentName];
  return DEFAULT_META;
}

// ---------------------------------------------------------------------------
// Parallel investigation, as seen from the trace: the "anomaly" step is the
// join of anomaly + network + security branches (agents/nodes/anomaly.py's
// anomaly_arbitrate), so its detail names every agent that investigated. The
// one-liner and the timeline both read it, so all three agents show up
// instead of just the one the step is named after.
// ---------------------------------------------------------------------------

interface InvestigatedAgent {
  agent: string;
  verdict?: string;
  durationMs?: number;
}

function investigatedAgents(steps?: AgentTraceStep[]): { agents: InvestigatedAgent[]; winner?: string; peak?: number } | null {
  const step = steps?.find((s) => s.node === "anomaly");
  if (!step) return null;
  const detail = step.detail as {
    investigated?: { agent: string; verdict?: string }[];
    contributing_agents?: string[];
    parallelism?: ParallelismSummary | null;
    winner?: string;
  };
  const base: { agent: string; verdict?: string }[] =
    detail.investigated?.length
      ? detail.investigated
      : (detail.contributing_agents ?? []).map((agent) => ({ agent }));
  if (base.length < 2) return null;

  // Longest branch per agent (an agent runs once per node in scope).
  const longest: Record<string, number> = {};
  for (const b of detail.parallelism?.branches ?? []) {
    longest[b.agent] = Math.max(longest[b.agent] ?? 0, b.duration_ms);
  }
  return {
    agents: base.map((a) => ({ ...a, durationMs: longest[a.agent] })),
    winner: detail.winner,
    peak: detail.parallelism?.peak_concurrency,
  };
}

/** "Anomaly agent" -> "Anomaly" so a list reads "Anomaly, Network and Security agents". */
function shortAgentLabel(agent: string) {
  return agentMeta(agent).label.replace(/\s+agent$/i, "");
}

const AGENT_ORDER = ["anomaly", "network", "security"];

function AgentNameList({ agents }: { agents: string[] }) {
  // Stable reading order (not the ranking order the timeline uses).
  const ordered = [...agents].sort((a, b) => AGENT_ORDER.indexOf(a) - AGENT_ORDER.indexOf(b));
  return (
    <>
      {ordered.map((agent, i) => (
        <span key={agent}>
          <span style={{ color: agentMeta(agent).color, fontWeight: 600 }}>{shortAgentLabel(agent)}</span>
          {i < ordered.length - 2 ? ", " : i === ordered.length - 2 ? " and " : ""}
        </span>
      ))}{" "}
      agents
    </>
  );
}

// ---------------------------------------------------------------------------
// Reasoning trace -- shown above the answer. While a request is in flight it
// cycles through generic staged copy (there's no token-level reasoning
// stream from the orchestrator yet, see routers/agents.py); once the answer
// lands it collapses into one line naming which agent actually handled it,
// the same way a "thought for Ns" summary would.
// ---------------------------------------------------------------------------

const THINKING_STEPS = [
  "Reading your question",
  "Routing to a specialist agent",
  "Gathering data",
  "Composing the answer",
];

export function ReasoningTrace({
  active,
  agentUsed,
  elapsedMs,
  steps,
}: {
  active: boolean;
  agentUsed?: string;
  elapsedMs?: number;
  // v0.11: the real per-node pipeline for this turn (see AgentTraceTimeline
  // above). When present, the collapsed one-liner below becomes a toggle
  // that expands into it -- "routed to the network agent" stops being the
  // whole story and becomes the summary of a real, inspectable sequence.
  steps?: AgentTraceStep[];
}) {
  const [step, setStep] = useState(0);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setStep((s) => (s + 1) % THINKING_STEPS.length), 700);
    return () => clearInterval(id);
  }, [active]);

  if (active) {
    return (
      <div className="reasoning-trace">
        <Loader2 className="h-3 w-3 shrink-0 animate-spin" style={{ color: "var(--text-muted)" }} strokeWidth={2} />
        <AnimatePresence mode="wait">
          <motion.span
            key={step}
            initial={{ opacity: 0, y: 3 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -3 }}
            transition={{ duration: 0.16 }}
          >
            {THINKING_STEPS[step]}…
          </motion.span>
        </AnimatePresence>
      </div>
    );
  }

  if (!agentUsed) return null;
  const meta = agentMeta(agentUsed);
  const Icon = meta.icon;
  const hasRealSteps = !!steps && steps.length > 0;
  // The last non-structural step before compose is what actually produced
  // the answer -- if that's a different agent than target_agent (i.e. it
  // got chained, see openstack_expert.py), say so instead of only naming
  // the agent the router originally picked.
  const chainedInto = hasRealSteps
    ? steps!.find((s) => s.node === "openstack_expert" && s.detail?.chained_from)
    : undefined;
  const parallel = hasRealSteps ? investigatedAgents(steps) : null;

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.2 }}>
      <button
        type="button"
        onClick={() => hasRealSteps && setExpanded((e) => !e)}
        className="reasoning-trace reasoning-trace--done"
        style={{ cursor: hasRealSteps ? "pointer" : "default", background: "none", border: "none", padding: 0 }}
        aria-expanded={expanded}
      >
        <Icon className="h-3 w-3 shrink-0" style={{ color: meta.color }} strokeWidth={2} />
        <span>
          {parallel && chainedInto ? (
            <>
              <AgentNameList agents={parallel.agents.map((a) => a.agent)} /> investigated in parallel
              {parallel.winner && (
                <>
                  {" "}
                  — the{" "}
                  <span style={{ color: agentMeta(parallel.winner).color, fontWeight: 600 }}>
                    {shortAgentLabel(parallel.winner).toLowerCase()}
                  </span>{" "}
                  agent&apos;s theory was best supported
                </>
              )}
              , so it was handed to the{" "}
              <span style={{ color: meta.color, fontWeight: 600 }}>{meta.label.toLowerCase()}</span>
            </>
          ) : parallel ? (
            <>
              <AgentNameList agents={parallel.agents.map((a) => a.agent)} /> investigated in parallel
              {parallel.winner && (
                <>
                  {" "}
                  — best-supported theory:{" "}
                  <span style={{ color: agentMeta(parallel.winner).color, fontWeight: 600 }}>
                    {shortAgentLabel(parallel.winner).toLowerCase()}
                  </span>{" "}
                  agent
                </>
              )}
            </>
          ) : chainedInto ? (
            <>
              <span style={{ color: agentMeta(chainedInto.detail.chained_from).color, fontWeight: 600 }}>
                {agentMeta(chainedInto.detail.chained_from).label}
              </span>{" "}
              found something worth walking through, so it was handed to the{" "}
              <span style={{ color: meta.color, fontWeight: 600 }}>{meta.label.toLowerCase()}</span>
            </>
          ) : (
            <>
              Routed to the <span style={{ color: meta.color, fontWeight: 600 }}>{meta.label.toLowerCase()}</span>
            </>
          )}
          {typeof elapsedMs === "number" && elapsedMs > 0 ? ` · ${(elapsedMs / 1000).toFixed(1)}s` : ""}
        </span>
        {hasRealSteps && (
          <ChevronDown
            className="h-3 w-3 shrink-0 transition-transform"
            style={{ color: "var(--text-muted)", transform: expanded ? "rotate(180deg)" : "none" }}
            strokeWidth={2}
          />
        )}
      </button>
      {hasRealSteps && expanded && (
        <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} transition={{ duration: 0.15 }}>
          <AgentTraceTimeline steps={steps} />
        </motion.div>
      )}
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// Agent trace timeline -- the *real* router -> agent [-> chained agent] ->
// critic -> compose pipeline for one turn (routers/agents.py now inlines
// agents/trace.py's step list on AgentOrchestrateResponse, see lib/types.ts's
// AgentTraceStep). This is what answers "did it actually call the network
// before handing off to the expert agent, or is that just what the
// collapsed one-liner claims" -- every step here is something a node in
// services/api/app/agents/graph.py genuinely ran, with the same summary
// text compose.py built the final answer from, not a re-narrated guess.
// ---------------------------------------------------------------------------

const STRUCTURAL_STEP_META: Record<string, { label: string; icon: typeof Activity; color: string; soft: string }> = {
  router: { label: "Router", icon: Route, color: "var(--text-dim)", soft: "var(--canvas)" },
  critic: { label: "Critic", icon: ShieldCheck, color: "var(--text-dim)", soft: "var(--canvas)" },
  compose: { label: "Compose", icon: Sparkles, color: "var(--accent)", soft: "var(--accent-soft)" },
};

function traceStepMeta(node: string) {
  if (node in STRUCTURAL_STEP_META) return STRUCTURAL_STEP_META[node];
  const meta = agentMeta(node);
  return { label: meta.label, icon: meta.icon, color: meta.color, soft: meta.soft };
}

/** node ids are the graph's internal names (e.g. "anomaly_arbitrate" for
 * what the trace still records as "anomaly", see graph.py's v0.8 note) --
 * this only has to cover the handful that don't already match an
 * AGENT_META/STRUCTURAL_STEP_META key verbatim. */
function traceStepDisplayName(node: string): string {
  return traceStepMeta(node).label;
}

function criticVerdictLabel(detail: AgentTraceStep["detail"]) {
  const v = detail.critic_verdict;
  if (!v) return null;
  return typeof v === "string" ? v : v.status;
}

/** The one or two lines shown when a step is expanded -- always built from
 * fields the backend actually sent (detail), never invented client-side. */
function TraceStepDetail({ step }: { step: AgentTraceStep }) {
  const { node, detail } = step;

  if (node === "router") {
    return (
      <span>
        Classified this as an{" "}
        <span style={{ color: "var(--text)", fontWeight: 600 }}>{detail.intent ?? "unknown"}</span> question and
        routed it to{" "}
        <span style={{ color: "var(--text)", fontWeight: 600 }}>{detail.target_agent ?? "—"}</span>.
      </span>
    );
  }

  if (node === "critic") {
    const verdict = criticVerdictLabel(detail);
    if (!verdict) return <span>No claims to check (a clarify/error turn never reaches this step&apos;s real work).</span>;
    return (
      <span>
        Evidence-grounding check:{" "}
        <span style={{ color: verdict === "flagged" ? "var(--warn)" : "var(--ok)", fontWeight: 600 }}>
          {verdict}
        </span>
        {verdict === "flagged" ? " — at least one claim in the answer wasn't traceable to the evidence gathered for it." : "."}
      </span>
    );
  }

  const parallel = detail.parallelism as ParallelismSummary | null | undefined;

  return (
    <div className="flex flex-col gap-1">
      {detail.chained_from && (
        <span className="italic" style={{ color: "var(--text-muted)" }}>
          Triggered by the {agentMeta(detail.chained_from).label.toLowerCase()}
        &apos;s finding, one step up.
        </span>
      )}
      {parallel && (
        <span className="italic" style={{ color: "var(--text-muted)" }}>
          {parallel.branch_count} agent branches on {parallel.threads} thread{parallel.threads === 1 ? "" : "s"} · peak
          concurrency {parallel.peak_concurrency} · {formatMs(parallel.wall_ms)} wall-clock vs{" "}
          {formatMs(parallel.sequential_ms)} sequential
          {typeof detail.winner === "string" ? ` · best-supported theory: ${detail.winner} agent` : ""}
        </span>
      )}
      {detail.summary && <span>{detail.summary}</span>}
      {typeof detail.confidence === "number" && (
        <span style={{ color: "var(--text-muted)" }}>Confidence: {(detail.confidence * 100).toFixed(0)}%</span>
      )}
      {detail.error && <span style={{ color: "var(--crit)" }}>Error: {detail.error}</span>}
    </div>
  );
}

export function AgentTraceTimeline({ steps }: { steps?: AgentTraceStep[] }) {
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  if (!steps || steps.length === 0) return null;
  const parallelInfo = investigatedAgents(steps);

  return (
    <div className="agent-trace-timeline">
      {steps.map((step, i) => {
        const meta = traceStepMeta(step.node);
        const Icon = meta.icon;
        const failed = step.status === "error";
        const hasDetail = Object.keys(step.detail || {}).length > 0;
        const isOpen = openIndex === i;
        return (
          <div key={`${step.node}-${i}`} className="agent-trace-timeline__row">
            {i > 0 && <div className="agent-trace-timeline__connector" aria-hidden />}
            <button
              type="button"
              onClick={() => hasDetail && setOpenIndex(isOpen ? null : i)}
              className="agent-trace-timeline__step"
              aria-expanded={isOpen}
              disabled={!hasDetail}
              style={{ cursor: hasDetail ? "pointer" : "default" }}
            >
              <span
                className="agent-trace-timeline__icon"
                style={{ background: failed ? "var(--crit-soft)" : meta.soft }}
              >
                {failed ? (
                  <XCircle className="h-3 w-3" style={{ color: "var(--crit)" }} strokeWidth={2} />
                ) : (
                  <Icon className="h-3 w-3" style={{ color: meta.color }} strokeWidth={2} />
                )}
              </span>
              <span className="agent-trace-timeline__label" style={{ color: failed ? "var(--crit)" : "var(--text-dim)" }}>
                {traceStepDisplayName(step.node)}
              </span>
              <span className="agent-trace-timeline__duration">{Math.round(step.duration_ms)}ms</span>
              {hasDetail && (
                <ChevronDown
                  className="h-3 w-3 shrink-0 transition-transform"
                  style={{ color: "var(--text-muted)", transform: isOpen ? "rotate(180deg)" : "none" }}
                  strokeWidth={2}
                />
              )}
            </button>
            {step.node === "anomaly" && parallelInfo && (
              <div className="agent-trace-timeline__branches" aria-label="Agents that investigated in parallel">
                {parallelInfo.agents.map((a) => {
                  const m = agentMeta(a.agent);
                  const BranchIcon = a.verdict === "winner" ? Crown : m.icon;
                  const v = a.verdict ? VERDICT_STYLE[a.verdict] : undefined;
                  return (
                    <div key={a.agent} className="agent-trace-timeline__branch">
                      <span className="agent-trace-timeline__icon" style={{ background: m.soft, width: 16, height: 16 }}>
                        <BranchIcon className="h-[11px] w-[11px]" style={{ color: m.color }} strokeWidth={2} />
                      </span>
                      <span className="agent-trace-timeline__label" style={{ color: m.color }}>
                        {m.label}
                      </span>
                      {v && (
                        <span className="agent-pill" style={{ color: v.color, background: v.soft }}>
                          {v.label}
                        </span>
                      )}
                      {typeof a.durationMs === "number" && (
                        <span className="agent-trace-timeline__duration">{formatMs(a.durationMs)}</span>
                      )}
                    </div>
                  );
                })}
                {typeof parallelInfo.peak === "number" && (
                  <div className="agent-trace-timeline__branch-note">
                    <Zap className="h-3 w-3" strokeWidth={2} /> ran in parallel · peak concurrency {parallelInfo.peak}
                  </div>
                )}
              </div>
            )}
            {isOpen && hasDetail && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                transition={{ duration: 0.15 }}
                className="agent-trace-timeline__detail"
              >
                <TraceStepDetail step={step} />
              </motion.div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared markdown renderer (answer text). Moved here from CopilotChat.tsx so
// both the plain-text fallback and every agent panel below can reuse it.
// ---------------------------------------------------------------------------

const CITE_SCHEME = "cite:";

function withCiteLinks(text: string) {
  return text.replace(/\[([a-zA-Z0-9_.\-/]+\.md)\]/g, `[$1](${CITE_SCHEME}$1)`);
}

/** Older stored answers carry the compose-step notes as a plain italic
 * paragraph (`_Note: ..._`). Rewrites them into the same `> [!WARNING]`
 * callout new answers ship with, so history looks the same as fresh turns. */
function withCallouts(text: string) {
  const brief = (t: string, n = 140) => {
    const flat = t.replace(/[`|*_>#[\]]+/g, "").replace(/\s+/g, " ").trim();
    return flat.length <= n ? flat : `${flat.slice(0, n - 1).trimEnd()}…`;
  };
  return text
    .replace(
      /^_Note: this answer contains at least one claim \("([\s\S]*?)"\) that could not be verified[\s\S]*?extra caution\._/gm,
      (_m, claim: string) =>
        `> [!WARNING]\n> **Unverified claim** — this answer contains a claim that could not be verified against the evidence gathered for it. Treat with extra caution.\n> First flagged: \u201c${brief(claim)}\u201d`,
    )
    .replace(/^_Note: ([^\n]+?)_$/gm, (_m, body: string) => `> [!WARNING]\n> ${body}`);
}

type MdNode = { type: string; value?: string; children?: MdNode[]; data?: Record<string, unknown> };
const CALLOUT_RE = /^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*/i;

/** Minimal remark plugin (no extra dependency): a blockquote whose first
 * line is `[!KIND]` becomes `<div class="md-callout md-callout--kind">`. */
function remarkCallouts() {
  const visit = (node: MdNode) => {
    const para = node.type === "blockquote" ? node.children?.[0] : undefined;
    const first = para?.type === "paragraph" ? para.children?.[0] : undefined;
    if (node.type === "blockquote" && first?.type === "text" && first.value) {
      const m = first.value.match(CALLOUT_RE);
      if (m) {
        const kind = m[1].toLowerCase() === "caution" || m[1].toLowerCase() === "important" ? "warning" : m[1].toLowerCase();
        first.value = first.value.slice(m[0].length);
        if (!first.value) para!.children!.shift();
        node.data = { hName: "div", hProperties: { className: ["md-callout", `md-callout--${kind}`] } };
      }
    }
    node.children?.forEach(visit);
  };
  return (tree: MdNode) => visit(tree);
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

function nodeText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement(node)) return nodeText((node.props as { children?: ReactNode }).children);
  return "";
}

function CodeBlock({ lang, code }: { lang?: string; code: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // Clipboard unavailable -- the block stays selectable by hand.
    }
  }
  const label = !lang || lang === "bash" || lang === "sh" || lang === "shell" ? "shell" : lang;
  return (
    <div className="md-codeblock">
      <div className="md-codeblock__bar">
        <span className="inline-flex items-center gap-1.5">
          <Terminal className="h-3 w-3" strokeWidth={2} />
          {label}
        </span>
        <button type="button" onClick={copy} aria-label="Copy" title="Copy" className="md-codeblock__copy">
          {copied ? <Check className="h-3 w-3" style={{ color: "var(--ok)" }} /> : <Copy className="h-3 w-3" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  );
}

const CALLOUT_ICON = { note: Info, tip: Lightbulb, warning: AlertTriangle } as const;

export function Markdown({ text }: { text: string }) {
  return (
    <div className="md-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkCallouts]}
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
          table({ children }) {
            return (
              <div className="md-table-wrap">
                <table>{children}</table>
              </div>
            );
          },
          pre({ children }) {
            const child = Array.isArray(children) ? children[0] : children;
            const props = (isValidElement(child) ? child.props : {}) as { className?: string; children?: ReactNode };
            const lang = /language-([\w-]+)/.exec(props.className ?? "")?.[1];
            return <CodeBlock lang={lang} code={nodeText(props.children).replace(/\n$/, "")} />;
          },
          div({ className, children }) {
            const kind = /md-callout--(\w+)/.exec(className ?? "")?.[1] as keyof typeof CALLOUT_ICON | undefined;
            if (!kind) return <div className={className}>{children}</div>;
            const Icon = CALLOUT_ICON[kind] ?? Info;
            return (
              <div className={className} role={kind === "warning" ? "alert" : "note"}>
                <Icon className="md-callout__icon" strokeWidth={2} />
                <div className="md-callout__body">{children}</div>
              </div>
            );
          },
        }}
      >
        {withCallouts(withCiteLinks(text))}
      </ReactMarkdown>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Monitoring panel -- live status tiles from LiveMetrics-shaped raw_data.
// ---------------------------------------------------------------------------

function pctTone(value: number) {
  if (value >= 90) return "var(--crit)";
  if (value >= 70) return "var(--warn)";
  return "var(--ok)";
}

function StatBar({ label, value, icon: Icon }: { label: string; value: number; icon: typeof Cpu }) {
  const tone = pctTone(value);
  return (
    <div className="agent-stat-tile">
      <div className="agent-stat-tile__head">
        <Icon className="h-3.5 w-3.5" style={{ color: "var(--text-muted)" }} strokeWidth={1.9} />
        <span>{label}</span>
        <span className="ml-auto font-mono font-semibold" style={{ color: tone }}>
          {value.toFixed(1)}%
        </span>
      </div>
      <div className="agent-stat-tile__bar">
        <div
          className="agent-stat-tile__bar-fill"
          style={{ width: `${Math.min(100, Math.max(0, value))}%`, background: tone }}
        />
      </div>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="agent-mini-stat">
      <div className="agent-mini-stat__label">{label}</div>
      <div className="agent-mini-stat__value">{value}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Multi-node (fleet) panels -- monitoring/prediction answers about several
// nodes at once (raw_data.scope === "multi"). The answer text already carries
// the comparison table; these add the at-a-glance version: one row per node,
// worst first (the backend sorts), with severity-colored bars.
// ---------------------------------------------------------------------------

function isFleet(data: unknown): boolean {
  return !!data && typeof data === "object" && (data as { scope?: string }).scope === "multi";
}

function MiniBar({ label, value }: { label: string; value: number }) {
  const tone = pctTone(value);
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between text-[10.5px] text-text-muted">
        <span>{label}</span>
        <span className="font-mono font-semibold" style={{ color: tone }}>
          {value}%
        </span>
      </div>
      <div className="mt-0.5 h-1 overflow-hidden rounded-full" style={{ background: "var(--border-soft)" }}>
        <div className="h-full rounded-full" style={{ width: `${Math.min(100, Math.max(0, value))}%`, background: tone }} />
      </div>
    </div>
  );
}

function FleetSummaryPill({ color, soft, children }: { color: string; soft: string; children: ReactNode }) {
  return (
    <span className="agent-pill" style={{ color, background: soft }}>
      {children}
    </span>
  );
}

function MonitoringFleetPanel({ data }: { data: AgentMonitoringFleetData }) {
  const { counts } = data;
  const attention = counts.down + counts.warning + counts.critical;
  return (
    <div className="agent-panel" style={{ borderColor: "color-mix(in srgb, var(--ok) 22%, var(--border))" }}>
      <div className="agent-panel__header">
        <div className="flex items-center gap-2">
          <Activity className="h-3.5 w-3.5" style={{ color: "var(--ok)" }} strokeWidth={1.9} />
          <span className="font-display text-[13px] font-semibold text-color-text">Fleet · {counts.total} nodes</span>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          <FleetSummaryPill color="var(--ok)" soft="var(--ok-soft)">
            <CheckCircle2 className="h-3 w-3" strokeWidth={2} />
            {counts.up} up
          </FleetSummaryPill>
          {counts.down > 0 && (
            <FleetSummaryPill color="var(--crit)" soft="var(--crit-soft)">
              <XCircle className="h-3 w-3" strokeWidth={2} />
              {counts.down} down
            </FleetSummaryPill>
          )}
          {attention > 0 && (
            <FleetSummaryPill color="var(--warn)" soft="var(--warn-soft)">
              <AlertTriangle className="h-3 w-3" strokeWidth={2} />
              {data.concerning.length} need attention
            </FleetSummaryPill>
          )}
        </div>
      </div>

      <ul className="flex flex-col gap-1.5">
        {data.nodes.map((n) => {
          const up = n.status === "up";
          const tone = !up || n.health === "critical" ? "var(--crit)" : n.health === "warning" ? "var(--warn)" : "var(--ok)";
          return (
            <li
              key={n.node}
              className="grid grid-cols-1 items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-2 sm:grid-cols-[minmax(0,1.3fr)_repeat(3,minmax(0,1fr))]"
              style={{ background: "var(--canvas)", borderLeft: `3px solid ${tone}` }}
            >
              <div className="min-w-0">
                <div className="truncate text-[12.5px] font-semibold text-color-text">{n.node}</div>
                <div className="text-[11px] text-text-muted">
                  {n.role} · {up ? n.health : "down"} · up {n.uptime}
                </div>
              </div>
              <MiniBar label="CPU" value={n.cpu_percent} />
              <MiniBar label="RAM" value={n.memory_percent} />
              <MiniBar label="Disk" value={n.disk_percent} />
            </li>
          );
        })}
      </ul>

      {data.missing.length > 0 && (
        <div className="flex items-start gap-2 text-[11.5px] text-text-muted">
          <AlertTriangle className="mt-[1px] h-3 w-3 shrink-0" style={{ color: "var(--warn)" }} strokeWidth={2} />
          No live data yet for {data.missing.join(", ")}.
        </div>
      )}
    </div>
  );
}

function PredictionFleetPanel({ data }: { data: AgentPredictionFleetData }) {
  const isPercent = data.metric.endsWith("_percent");
  const unit = isPercent ? "%" : "";
  return (
    <div className="agent-panel" style={{ borderColor: "color-mix(in srgb, var(--medium) 22%, var(--border))" }}>
      <div className="agent-panel__header">
        <div className="flex items-center gap-2">
          <TrendingUp className="h-3.5 w-3.5" style={{ color: "var(--medium)" }} strokeWidth={1.9} />
          <span className="font-display text-[13px] font-semibold text-color-text">
            {humanizeMetric(data.metric)} · {data.counts.total} nodes
          </span>
        </div>
        <FleetSummaryPill
          color={data.at_risk.length ? "var(--crit)" : "var(--medium)"}
          soft={data.at_risk.length ? "var(--crit-soft)" : "var(--medium-soft)"}
        >
          {data.at_risk.length > 0 && <AlertTriangle className="h-3 w-3" strokeWidth={2} />}
          {data.at_risk.length ? `${data.at_risk.length} at risk` : data.horizon_days ? `${data.horizon_days}d forecast` : "forecast"}
        </FleetSummaryPill>
      </div>

      <ul className="flex flex-col gap-1.5">
        {data.nodes.map((n) => {
          const tone = n.will_breach ? "var(--crit)" : n.may_breach ? "var(--warn)" : "var(--ok)";
          const TrendIcon = n.delta > 0.5 ? TrendingUp : n.delta < -0.5 ? TrendingDown : Minus;
          return (
            <li
              key={n.hostname}
              className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,1.6fr)_auto] items-center gap-3 rounded-[var(--radius-control)] px-2.5 py-2"
              style={{ background: "var(--canvas)", borderLeft: `3px solid ${tone}` }}
            >
              <div className="min-w-0">
                <div className="truncate text-[12.5px] font-semibold text-color-text">{n.hostname}</div>
                <div className="text-[11px] text-text-muted">{n.role}</div>
              </div>
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 font-mono text-[11.5px] text-text-dim">
                  {n.start}
                  {unit} → <span style={{ color: tone, fontWeight: 600 }}>{n.end}{unit}</span>
                  <TrendIcon className="h-3 w-3" style={{ color: "var(--text-muted)" }} strokeWidth={2} />
                </div>
                {isPercent && (
                  <div className="mt-1 h-1 overflow-hidden rounded-full" style={{ background: "var(--border-soft)" }}>
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${Math.min(100, Math.max(0, n.end))}%`, background: tone }}
                    />
                  </div>
                )}
              </div>
              <span className="agent-pill shrink-0" style={{ color: tone, background: "var(--canvas)", border: `1px solid ${tone}` }}>
                {n.will_breach ? "will cross" : n.may_breach ? "may cross" : "ok"}
              </span>
            </li>
          );
        })}
      </ul>

      {data.missing.length > 0 && (
        <div className="flex items-start gap-2 text-[11.5px] text-text-muted">
          <AlertTriangle className="mt-[1px] h-3 w-3 shrink-0" style={{ color: "var(--warn)" }} strokeWidth={2} />
          Not enough data to forecast {data.missing.join(", ")}.
        </div>
      )}
    </div>
  );
}

function MonitoringPanel({ data }: { data: AgentMonitoringData }) {
  const healthy = data.health === "healthy";
  const warning = data.health === "warning";
  const up = data.status === "up";

  return (
    <div className="agent-panel" style={{ borderColor: "color-mix(in srgb, var(--ok) 22%, var(--border))" }}>
      <div className="agent-panel__header">
        <div className="flex items-center gap-2">
          <span className="font-display text-[13px] font-semibold text-color-text">{data.node}</span>
          <span className="text-[11px] text-text-muted">{data.instance}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className="agent-pill"
            style={{
              color: up ? "var(--ok)" : "var(--crit)",
              background: up ? "var(--ok-soft)" : "var(--crit-soft)",
            }}
          >
            {up ? <CheckCircle2 className="h-3 w-3" strokeWidth={2} /> : <XCircle className="h-3 w-3" strokeWidth={2} />}
            {up ? "up" : "down"}
          </span>
          <span
            className="agent-pill"
            style={{
              color: healthy ? "var(--ok)" : warning ? "var(--warn)" : "var(--crit)",
              background: healthy ? "var(--ok-soft)" : warning ? "var(--warn-soft)" : "var(--crit-soft)",
            }}
          >
            {!healthy && <AlertTriangle className="h-3 w-3" strokeWidth={2} />}
            {data.health}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <StatBar label="CPU" value={data.cpu_percent} icon={Cpu} />
        <StatBar label="Memory" value={data.memory_percent} icon={MemoryStick} />
        <StatBar label="Disk" value={data.disk_percent} icon={HardDrive} />
        <StatBar label="Swap" value={data.swap_percent} icon={Gauge} />
      </div>

      <div className="agent-mini-stat-grid">
        <MiniStat label="Load 1 / 5 / 15" value={`${data.load1.toFixed(2)} / ${data.load5.toFixed(2)} / ${data.load15.toFixed(2)}`} />
        <MiniStat label="Uptime" value={data.uptime} />
        <MiniStat label="Processes" value={`${data.procs_running} running · ${data.procs_blocked} blocked`} />
        <MiniStat label="Disk I/O" value={`↓ ${data.disk_read} · ↑ ${data.disk_write}`} />
        <MiniStat label="Network" value={`↓ ${data.network_rx} · ↑ ${data.network_tx}`} />
        <MiniStat label="Role" value={data.role} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Network agent panel -- node-level interface counters + Neutron control-
// plane health (scope: "node", the common case -- see nodes/network.py),
// or a single Neutron entity's reading (scope: "network"/"subnet"/
// "instance", Phase C). Mirrors MonitoringPanel's stat-tile layout for the
// node-scoped case; the entity-scoped case is a simpler reading card, plus
// a "View topology" button for scope === "network" that opens the same
// real topology diagram the /networks page uses (components/
// NetworkTopologyDiagram.tsx, backed by GET /api/topology/networks/{id}/
// diagram) rather than a client-drawn approximation.
// ---------------------------------------------------------------------------

function rateTone(perSec: number) {
  if (perSec > 1) return "var(--crit)";
  if (perSec > 0) return "var(--warn)";
  return "var(--ok)";
}

function formatRate(bytesPerSec: number) {
  if (bytesPerSec >= 1024 * 1024) return `${(bytesPerSec / (1024 * 1024)).toFixed(2)} MB/s`;
  if (bytesPerSec >= 1024) return `${(bytesPerSec / 1024).toFixed(2)} KB/s`;
  return `${bytesPerSec.toFixed(0)} B/s`;
}

/** One node in the little host -> agent -> gateway status chain --
 * deliberately not a full topology render (we only have this one host's
 * Neutron reading, not the whole network graph -- that's what the "View
 * topology" button on the network-scoped card is for instead). */
function FlowNode({
  icon: Icon,
  label,
  sublabel,
  ok,
  unknown,
}: {
  icon: typeof Cpu;
  label: string;
  sublabel?: string;
  ok: boolean;
  unknown?: boolean;
}) {
  const color = unknown ? "var(--text-muted)" : ok ? "var(--chart-6)" : "var(--crit)";
  const soft = unknown ? "var(--canvas)" : ok ? "rgba(43,158,158,0.12)" : "var(--crit-soft)";
  return (
    <div
      className="flex min-w-[104px] flex-col items-center gap-1 rounded-[var(--radius-control)] px-2.5 py-2 text-center"
      style={{ background: soft, border: `1px solid ${unknown ? "var(--border-soft)" : "transparent"}` }}
    >
      <Icon className="h-4 w-4" style={{ color }} strokeWidth={2} />
      <span className="text-[11px] font-semibold leading-tight text-color-text">{label}</span>
      {sublabel && (
        <span className="text-[10px] leading-tight" style={{ color }}>
          {sublabel}
        </span>
      )}
    </div>
  );
}

function FlowConnector({ ok }: { ok: boolean }) {
  return (
    <div className="flex w-6 shrink-0 items-center justify-center sm:w-8">
      <div className="h-px w-full" style={{ background: ok ? "var(--chart-6)" : "var(--crit)", opacity: 0.5 }} />
    </div>
  );
}

function NeutronAgentChip({ agent }: { agent: NeutronAgentStatus }) {
  const ok = agent.alive && agent.admin_state_up;
  return (
    <span
      className="agent-pill"
      style={{ color: ok ? "var(--chart-6)" : "var(--crit)", background: ok ? "rgba(43,158,158,0.1)" : "var(--crit-soft)" }}
      title={agent.host}
    >
      {ok ? <CheckCircle2 className="h-3 w-3" strokeWidth={2} /> : <XCircle className="h-3 w-3" strokeWidth={2} />}
      {agent.binary}
    </span>
  );
}

function NetworkProblemList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div
      className="flex items-start gap-2 rounded-[var(--radius-control)] px-2.5 py-2 text-[12px] leading-relaxed"
      style={{ background: "var(--crit-soft)", color: "var(--text-dim)" }}
    >
      <AlertTriangle className="mt-[1px] h-3.5 w-3.5 shrink-0" style={{ color: "var(--crit)" }} strokeWidth={2} />
      <span>
        <span className="font-semibold" style={{ color: "var(--crit)" }}>
          {title}:
        </span>{" "}
        {items.join(", ")}
      </span>
    </div>
  );
}

function NetworkNodeScopePanel({ data }: { data: AgentNetworkData }) {
  const metric = data.metric_signal;
  const neutron = data.neutron_signal;
  const metricsData = metric?.data;
  const agents = neutron?.data?.agents ?? [];
  const anyAgentDown = (neutron?.down_agents?.length ?? 0) > 0;
  const anyRouterBad = (neutron?.bad_routers?.length ?? 0) > 0;
  const hasRouters = (neutron?.data?.routers?.length ?? 0) > 0;
  const errorRate = metricsData?.network_errors_per_sec ?? 0;
  const dropRate = metricsData?.network_drops_per_sec ?? 0;

  return (
    <div className="agent-panel" style={{ borderColor: "color-mix(in srgb, var(--chart-6) 22%, var(--border))" }}>
      <div className="agent-panel__header">
        <div className="flex items-center gap-2">
          <Network className="h-3.5 w-3.5" style={{ color: "var(--chart-6)" }} strokeWidth={1.9} />
          <span className="font-display text-[13px] font-semibold text-color-text">{data.hostname}</span>
          <span className="text-[11px] text-text-muted">{data.role}</span>
        </div>
        {neutron?.degraded && (
          <span className="agent-pill" style={{ color: "var(--warn)", background: "var(--warn-soft)" }}>
            <AlertTriangle className="h-3 w-3" strokeWidth={2} />
            Neutron unreachable
          </span>
        )}
      </div>

      {/* host -> OVS agent -> gateway router status chain */}
      <div className="flex items-center justify-center overflow-x-auto py-1">
        <FlowNode icon={Cpu} label={data.hostname ?? "host"} sublabel="this node" ok unknown />
        <FlowConnector ok={!anyAgentDown} />
        <FlowNode
          icon={Router}
          label="Neutron agent(s)"
          sublabel={agents.length ? `${agents.length} on host` : "none registered"}
          ok={!anyAgentDown}
          unknown={agents.length === 0}
        />
        {hasRouters && (
          <>
            <FlowConnector ok={!anyRouterBad} />
            <FlowNode icon={Network} label="Router" sublabel={anyRouterBad ? "degraded" : "active"} ok={!anyRouterBad} />
          </>
        )}
      </div>

      <div className="agent-mini-stat-grid">
        <MiniStat label="RX throughput" value={metricsData ? formatRate(metricsData.network_rx_bytes) : "—"} />
        <MiniStat label="TX throughput" value={metricsData ? formatRate(metricsData.network_tx_bytes) : "—"} />
        <MiniStat
          label="Errors/sec"
          value={<span style={{ color: rateTone(errorRate) }}>{errorRate.toFixed(2)}</span>}
        />
        <MiniStat
          label="Dropped/sec"
          value={<span style={{ color: rateTone(dropRate) }}>{dropRate.toFixed(2)}</span>}
        />
      </div>

      {agents.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {agents.map((a) => (
            <NeutronAgentChip key={a.id} agent={a} />
          ))}
        </div>
      )}

      <NetworkProblemList
        title="Down/disabled agents"
        items={(neutron?.down_agents ?? []).map((a) => a.binary)}
      />
      <NetworkProblemList
        title="Routers not fully up"
        items={(neutron?.bad_routers ?? []).map((r) => r.name || r.id)}
      />
      <NetworkProblemList
        title="Instances with a down port"
        items={(neutron?.bad_instances ?? []).map((i) => i.name || i.id)}
      />
    </div>
  );
}

function NetworkEntityScopePanel({ data }: { data: AgentNetworkData }) {
  const entity = data.entity;
  const signal = data.entity_signal;
  const [showTopology, setShowTopology] = useState(false);
  const downInstances = signal?.down_instances ?? [];
  const kindLabel = entity?.kind ? entity.kind[0].toUpperCase() + entity.kind.slice(1) : "Entity";

  return (
    <div className="agent-panel" style={{ borderColor: "color-mix(in srgb, var(--chart-6) 22%, var(--border))" }}>
      <div className="agent-panel__header">
        <div className="flex items-center gap-2">
          <Network className="h-3.5 w-3.5" style={{ color: "var(--chart-6)" }} strokeWidth={1.9} />
          <span className="font-display text-[13px] font-semibold text-color-text">{entity?.name ?? entity?.id}</span>
          {entity?.cidr && <span className="text-[11px] text-text-muted">{entity.cidr}</span>}
        </div>
        <div className="flex items-center gap-1.5">
          <span className="agent-pill" style={{ color: "var(--chart-6)", background: "rgba(43,158,158,0.1)" }}>
            {kindLabel}
          </span>
          {signal?.degraded && (
            <span className="agent-pill" style={{ color: "var(--warn)", background: "var(--warn-soft)" }}>
              <AlertTriangle className="h-3 w-3" strokeWidth={2} />
              partial data
            </span>
          )}
        </div>
      </div>

      {signal?.detail && <p className="text-[12.5px] leading-relaxed text-text-dim">{signal.detail}</p>}

      <NetworkProblemList
        title="Instances with a down port"
        items={downInstances.map((i) => i.name || i.id)}
      />

      {entity?.kind === "network" && entity.id && (
        <button
          type="button"
          onClick={() => setShowTopology(true)}
          className="agent-pill w-fit"
          style={{ color: "var(--chart-6)", background: "rgba(43,158,158,0.1)", cursor: "pointer" }}
        >
          <Waypoints className="h-3 w-3" strokeWidth={2} />
          View full topology
        </button>
      )}

      {showTopology && entity?.id && (
        <NetworkTopologyDiagram networkId={entity.id} onClose={() => setShowTopology(false)} />
      )}
    </div>
  );
}

function NetworkPanel({ data }: { data: AgentNetworkData }) {
  if (data.scope === "node") return <NetworkNodeScopePanel data={data} />;
  return <NetworkEntityScopePanel data={data} />;
}

// ---------------------------------------------------------------------------
// Security agent panel -- five sub-checks (auth-anomaly, sec-group-diff,
// CVE-match, exposed-port cross-check, eBPF-signal) merged into one
// finding, see nodes/security.py. Every sub-signal renders the same way:
// a status chip (clean / flagged / unknown) plus its own detail line --
// except when `restricted` is set, which means this response was
// RBAC-filtered for a non-admin account (see routers/agents.py's
// `_redact_security_raw_data`); in that case the panel shows a clear
// "admin only" placeholder instead of trying to render fields the backend
// never sent.
// ---------------------------------------------------------------------------

export function SecuritySignalRow({
  icon: Icon,
  label,
  signal,
  children,
}: {
  icon: typeof Activity;
  label: string;
  signal: { has_signal: boolean; degraded?: boolean; detail?: string; restricted?: boolean };
  children?: ReactNode;
}) {
  const tone = signal.restricted
    ? "var(--text-muted)"
    : signal.degraded
    ? "var(--warn)"
    : signal.has_signal
    ? "var(--crit)"
    : "var(--ok)";
  const soft = signal.restricted
    ? "var(--canvas)"
    : signal.degraded
    ? "var(--warn-soft)"
    : signal.has_signal
    ? "var(--crit-soft)"
    : "var(--ok-soft)";
  const statusLabel = signal.restricted ? "Admin only" : signal.degraded ? "Unknown" : signal.has_signal ? "Flagged" : "Clean";

  return (
    <div className="flex flex-col gap-1 rounded-[var(--radius-control)] px-2.5 py-2" style={{ background: "var(--canvas)", border: "1px solid var(--border-soft)" }}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Icon className="h-3.5 w-3.5" style={{ color: tone }} strokeWidth={1.9} />
          <span className="text-[12px] font-semibold text-color-text">{label}</span>
        </div>
        <span className="agent-pill" style={{ color: tone, background: soft }}>
          {statusLabel}
        </span>
      </div>
      {signal.restricted ? (
        <p className="text-[11.5px] italic leading-relaxed" style={{ color: "var(--text-muted)" }}>
          Details restricted to admin accounts.
        </p>
      ) : (
        signal.detail && <p className="text-[11.5px] leading-relaxed text-text-dim">{signal.detail}</p>
      )}
      {!signal.restricted && children}
    </div>
  );
}

export function SecurityPanel({ data }: { data: AgentSecurityData }) {
  const anyRestricted = [data.auth_signal, data.sec_group_signal, data.cve_signal, data.exposed_port_signal, data.ebpf_signal].some((s) => s.restricted);

  return (
    <div className="agent-panel" style={{ borderColor: "color-mix(in srgb, var(--chart-4) 22%, var(--border))" }}>
      <div className="agent-panel__header">
        <div className="flex items-center gap-2">
          <Lock className="h-3.5 w-3.5" style={{ color: "var(--chart-4)" }} strokeWidth={1.9} />
          <span className="font-display text-[13px] font-semibold text-color-text">{data.hostname}</span>
          <span className="text-[11px] text-text-muted">{data.role}</span>
        </div>
        {anyRestricted && (
          <span className="agent-pill" style={{ color: "var(--text-muted)", background: "var(--canvas)" }}>
            <Lock className="h-3 w-3" strokeWidth={2} />
            Admin only
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        <SecuritySignalRow icon={Terminal} label="Auth activity" signal={data.auth_signal} />
        <SecuritySignalRow icon={Network} label="Security groups" signal={data.sec_group_signal}>
          {(data.sec_group_signal.risky_rules?.length ?? 0) > 0 && (
            <ul className="flex flex-col gap-0.5">
              {data.sec_group_signal.risky_rules!.slice(0, 3).map((r, i) => (
                <li key={i} className="text-[11px]" style={{ color: "var(--crit)" }}>
                  {r.security_group}: {r.reason}
                </li>
              ))}
            </ul>
          )}
          {/* Phase Sec-1: drift since the last stored snapshot, kept
             visually distinct (warn, not crit) from risky_rules above --
             a rule change isn't automatically a risk, it's a fact worth
             noticing on its own. */}
          {(data.sec_group_signal.drift?.length ?? 0) > 0 && (
            <ul className="flex flex-col gap-0.5">
              {data.sec_group_signal.drift!.slice(0, 3).map((d, i) => (
                <li key={i} className="text-[11px]" style={{ color: "var(--warn)" }}>
                  {d.security_group}: +{d.added_rules.length}/-{d.removed_rules.length} rule(s) since{" "}
                  {new Date(d.previous_captured_at).toLocaleString()}
                </li>
              ))}
            </ul>
          )}
        </SecuritySignalRow>
        <SecuritySignalRow icon={ScrollText} label="Known CVEs" signal={data.cve_signal}>
          {(data.cve_signal.matches?.length ?? 0) > 0 && (
            <ul className="flex flex-col gap-0.5">
              {data.cve_signal.matches!.slice(0, 3).map((m, i) => (
                <li key={i} className="text-[11px]" style={{ color: "var(--crit)" }}>
                  {m.cve_id} ({m.severity}) -- {m.package} {m.installed_version}
                </li>
              ))}
            </ul>
          )}
        </SecuritySignalRow>
        <SecuritySignalRow icon={Globe} label="Exposed ports" signal={data.exposed_port_signal}>
          {(data.exposed_port_signal.mismatches?.length ?? 0) > 0 && (
            <ul className="flex flex-col gap-0.5">
              {data.exposed_port_signal.mismatches!.slice(0, 3).map((m, i) => (
                <li key={i} className="text-[11px]" style={{ color: "var(--crit)" }}>
                  port {m.port} ({m.process ?? "unknown"}) via {m.security_group} -- {m.reason}
                </li>
              ))}
            </ul>
          )}
        </SecuritySignalRow>
        <SecuritySignalRow icon={Cpu} label="Kernel-level (eBPF)" signal={data.ebpf_signal}>
          {(data.ebpf_signal.alerts?.length ?? 0) > 0 && (
            <ul className="flex flex-col gap-0.5">
              {data.ebpf_signal.alerts!.slice(0, 3).map((a, i) => (
                <li key={i} className="text-[11px]" style={{ color: "var(--crit)" }}>
                  [{a.priority}] {a.rule}
                </li>
              ))}
            </ul>
          )}
        </SecuritySignalRow>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Prediction panel -- forecast chart (actual history + predicted band) built
// from the same series the API returns in raw_data.forecast/actual.
// ---------------------------------------------------------------------------

const PERCENT_CONCERN_THRESHOLD = 90;

function humanizeMetric(metric: string) {
  return metric
    .replace(/_percent$/, "")
    .replace(/_/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase())
    .concat(metric.endsWith("_percent") ? " usage" : "");
}

function shortTime(ms: number) {
  const d = new Date(ms);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay
    ? d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

interface ChartRow {
  tsMs: number;
  actual?: number;
  predicted?: number;
  band?: [number, number];
}

function buildSeries(data: AgentPredictionData): ChartRow[] {
  const rows: ChartRow[] = data.actual.map((a) => ({ tsMs: new Date(a.timestamp).getTime(), actual: a.value }));
  const lastActual = data.actual[data.actual.length - 1];
  if (lastActual) {
    // Bridge point so the dashed forecast line visually connects to the
    // solid actual line instead of leaving a gap.
    rows.push({
      tsMs: new Date(lastActual.timestamp).getTime(),
      predicted: lastActual.value,
      band: [lastActual.value, lastActual.value],
    });
  }
  for (const f of data.forecast as ForecastPoint[]) {
    rows.push({ tsMs: new Date(f.timestamp).getTime(), predicted: f.predicted, band: [f.lower, f.upper] });
  }
  return rows.sort((a, b) => a.tsMs - b.tsMs);
}

function PredictionPanel({ data }: { data: AgentPredictionData }) {
  const isPercent = data.metric.endsWith("_percent");
  const series = buildSeries(data);
  const first = data.forecast[0];
  const last = data.forecast[data.forecast.length - 1];
  const delta = first && last ? last.predicted - first.predicted : 0;
  const trendIcon = delta > 0.5 ? TrendingUp : delta < -0.5 ? TrendingDown : Minus;
  const TrendIcon = trendIcon;
  const willBreach = isPercent && data.forecast.some((f) => f.upper >= PERCENT_CONCERN_THRESHOLD);
  const metricLabel = humanizeMetric(data.metric);

  return (
    <div className="agent-panel" style={{ borderColor: "color-mix(in srgb, var(--medium) 22%, var(--border))" }}>
      <div className="agent-panel__header">
        <div className="flex items-center gap-2">
          <span className="font-display text-[13px] font-semibold text-color-text">{metricLabel}</span>
          <span className="text-[11px] text-text-muted">{data.hostname}</span>
        </div>
        <span
          className="agent-pill"
          style={{
            color: willBreach ? "var(--crit)" : "var(--medium)",
            background: willBreach ? "var(--crit-soft)" : "var(--medium-soft)",
          }}
        >
          {willBreach && <AlertTriangle className="h-3 w-3" strokeWidth={2} />}
          {willBreach ? "may cross 90%" : `${data.horizon_days}d forecast`}
        </span>
      </div>

      {first && last && (
        <div className="agent-forecast-headline">
          <div>
            <div className="agent-mini-stat__label">Now</div>
            <div className="agent-forecast-headline__value">
              {first.predicted.toFixed(1)}
              {isPercent ? "%" : ""}
            </div>
          </div>
          <TrendIcon
            className="h-4 w-4 shrink-0"
            style={{ color: delta > 0.5 ? "var(--crit)" : delta < -0.5 ? "var(--ok)" : "var(--text-muted)" }}
            strokeWidth={2.25}
          />
          <div>
            <div className="agent-mini-stat__label">In {data.horizon_days}d</div>
            <div className="agent-forecast-headline__value">
              {last.predicted.toFixed(1)}
              {isPercent ? "%" : ""}
            </div>
          </div>
        </div>
      )}

      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={series} margin={{ top: 6, right: 10, left: -18, bottom: 0 }}>
          <defs>
            <linearGradient id="prediction-band" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--medium)" stopOpacity={0.28} />
              <stop offset="100%" stopColor="var(--medium)" stopOpacity={0.04} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--border-soft)" vertical={false} />
          <XAxis
            dataKey="tsMs"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={shortTime}
            tick={{ fill: "var(--color-text-faint)", fontSize: 10 }}
            axisLine={{ stroke: "var(--border-soft)" }}
            tickLine={false}
            minTickGap={40}
          />
          <YAxis
            domain={isPercent ? [0, 100] : ["auto", "auto"]}
            tick={{ fill: "var(--color-text-faint)", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            width={36}
          />
          <Tooltip
            labelFormatter={(v) => (typeof v === "number" ? new Date(v).toLocaleString() : String(v ?? ""))}
            formatter={(val, name) => {
              if (name === "band" && Array.isArray(val)) {
                const [lo, hi] = val as [number, number];
                return [`${lo.toFixed(1)} – ${hi.toFixed(1)}`, "confidence range"];
              }
              if (typeof val === "number") return [val.toFixed(1), name === "actual" ? "actual" : "forecast"];
              return [String(val), String(name)];
            }}
            contentStyle={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-control)",
              fontSize: 12,
            }}
          />
          {isPercent && (
            <ReferenceLine
              y={PERCENT_CONCERN_THRESHOLD}
              stroke="var(--crit)"
              strokeDasharray="4 4"
              strokeOpacity={0.6}
              label={{ value: "concern threshold", fontSize: 10, fill: "var(--crit)", position: "insideTopRight" }}
            />
          )}
          <Area dataKey="band" stroke="none" fill="url(#prediction-band)" isAnimationActive={false} connectNulls />
          <Line
            dataKey="actual"
            stroke="var(--text-dim)"
            strokeWidth={2}
            dot={false}
            type="monotone"
            isAnimationActive={false}
          />
          <Line
            dataKey="predicted"
            stroke="var(--medium)"
            strokeWidth={2}
            strokeDasharray="5 4"
            dot={false}
            type="monotone"
            isAnimationActive={false}
            connectNulls
          />
        </ComposedChart>
      </ResponsiveContainer>

      <div className="flex items-center gap-1.5 text-[11px] text-text-muted">
        <Clock className="h-3 w-3" strokeWidth={1.75} />
        {data.model_type} · {data.n_points_used.toLocaleString()} points analyzed
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RAG panel -- source chips from raw_data.sources (score-ranked docs).
// ---------------------------------------------------------------------------

function RagPanel({ data }: { data: AgentRagData }) {
  if (!data.sources?.length) return null;
  return (
    <div className="mt-2.5 flex flex-wrap gap-1.5">
      {data.sources.map((s, i) => (
        <div
          key={`${s.source_path}-${i}`}
          title={`${s.doc_title} · relevance ${(s.score * 100).toFixed(0)}%`}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-2 py-1 text-[11px]"
          style={{ border: "1px solid var(--border-soft)", background: "var(--canvas)", color: "var(--text-faint)" }}
        >
          <FileText className="h-[11px] w-[11px]" strokeWidth={1.75} style={{ color: "var(--chart-2)" }} />
          <span className="font-medium text-color-text">{s.doc_title}</span>
          <span className="text-text-muted">· {s.source_path}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// OpenStack Expert panel -- one fixed-shape card per matched symptom
// (services/api/app/agents/nodes/openstack_expert.py's SymptomEntry): a
// title + category pill, then two command sections (confirm / remediate).
// Every command gets its own row with a copy button -- an operational
// command is something people actually copy out of the chat and run, not
// just read, so it doesn't stay folded into the prose the way the rest of
// the answer does. Renders nothing when matched_symptom_id is null (the
// graceful "nothing in the catalog matched" fallback, see _run_standalone).
// ---------------------------------------------------------------------------

const EXPERT_CATEGORY_LABEL: Record<string, string> = {
  compute: "Compute",
  storage: "Storage",
  network: "Network",
  identity: "Identity",
  image: "Image",
  "message-bus": "Message bus",
  database: "Database",
  hypervisor: "Hypervisor",
  host: "Host",
};

function CommandRow({ cmd }: { cmd: AgentExpertCommand }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(cmd.command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // Clipboard API unavailable (e.g. insecure context) -- the command
      // is still fully visible and selectable by hand, so fail silently.
    }
  }

  return (
    <div className="agent-command-row">
      <div className="agent-command-row__head">
        <p className="agent-command-row__desc">{cmd.description}</p>
        <span
          className="agent-pill shrink-0"
          style={
            cmd.read_only
              ? { color: "var(--ok)", background: "var(--ok-soft)" }
              : { color: "var(--warn)", background: "var(--warn-soft)" }
          }
        >
          {cmd.read_only ? (
            <ShieldCheck className="h-3 w-3" strokeWidth={2} />
          ) : (
            <AlertTriangle className="h-3 w-3" strokeWidth={2} />
          )}
          {cmd.read_only ? "read-only" : "state-changing"}
        </span>
      </div>
      <div className="agent-command-row__code">
        <code>{cmd.command}</code>
        <button onClick={copy} className="agent-command-row__copy" aria-label="Copy command" title="Copy command">
          {copied ? <Check className="h-3.5 w-3.5" style={{ color: "var(--ok)" }} /> : <Copy className="h-3.5 w-3.5" />}
        </button>
      </div>
    </div>
  );
}

function CommandSection({ title, commands }: { title: string; commands: AgentExpertCommand[] }) {
  if (!commands.length) return null;
  return (
    <div className="agent-anomaly-subcard">
      <div className="agent-anomaly-subcard__head">
        <Terminal className="h-3.5 w-3.5" style={{ color: "var(--text-muted)" }} strokeWidth={1.9} />
        {title}
      </div>
      <div className="flex flex-col gap-1.5">
        {commands.map((cmd, i) => (
          <CommandRow key={`${cmd.command}-${i}`} cmd={cmd} />
        ))}
      </div>
    </div>
  );
}

function hostOf(url: string) {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/** Sources card for the two non-catalog tiers (official docs / community
 * search). Official docs get the accent tone; community results get the
 * warning tone because they're unreviewed. */
function ExpertSourcesPanel({ data }: { data: AgentExpertData }) {
  const official = data.source === "official_docs";
  const items = official
    ? (data.doc_results ?? []).map((d) => ({
        title: d.heading && d.heading !== d.title ? `${d.title} — ${d.heading}` : d.title,
        url: d.url,
        meta: typeof d.score === "number" ? `${Math.round(d.score * 100)}% match` : undefined,
      }))
    : (data.web_results ?? []).map((w) => ({ title: w.title, url: w.url, meta: hostOf(w.url) }));
  if (items.length === 0) return null;

  const tone = official
    ? { color: "var(--accent)", soft: "var(--accent-soft)", label: "Official OpenStack docs" }
    : { color: "var(--warn)", soft: "var(--warn-soft)", label: "Community sources · unverified" };
  const Icon = official ? BookOpen : AlertTriangle;

  return (
    <div className="agent-panel" style={{ borderColor: `color-mix(in srgb, ${tone.color} 25%, var(--border))` }}>
      <div className="agent-panel__header">
        <div className="flex items-center gap-2">
          <Icon className="h-3.5 w-3.5" style={{ color: tone.color }} strokeWidth={1.9} />
          <span className="font-display text-[13px] font-semibold text-color-text">Sources</span>
        </div>
        <span className="agent-pill" style={{ color: tone.color, background: tone.soft }}>
          {tone.label}
        </span>
      </div>
      <ul className="flex flex-col gap-1.5">
        {items.map((item, i) => (
          <li key={`${item.url}-${i}`}>
            <a
              href={item.url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-1.5 text-[12px] no-underline"
              style={{ background: "var(--canvas)", color: "var(--text)" }}
            >
              <ExternalLink className="h-3 w-3 shrink-0" style={{ color: tone.color }} strokeWidth={2} />
              <span className="min-w-0 flex-1 truncate">{item.title}</span>
              {item.meta && <span className="shrink-0 text-[11px] text-text-muted">{item.meta}</span>}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Parallel incident investigation (v1.2) -- what the anomaly/network/security
// agents each found *at the same time*, and which theory arbitration picked.
// The timeline is drawn from measured per-branch start offsets/durations
// (incident_fanout.py), so overlapping bars are overlap that really happened,
// not an illustration.
// ---------------------------------------------------------------------------

const VERDICT_STYLE: Record<string, { label: string; color: string; soft: string }> = {
  winner: { label: "Best-supported", color: "var(--ok)", soft: "var(--ok-soft)" },
  also_flagged: { label: "Also flagged", color: "var(--warn)", soft: "var(--warn-soft)" },
  no_signal: { label: "No signal", color: "var(--text-muted)", soft: "var(--canvas)" },
  failed: { label: "Did not complete", color: "var(--crit)", soft: "var(--crit-soft)" },
};

function formatMs(ms: number) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function ArbitrationPanel({ arbitration }: { arbitration: IncidentArbitration }) {
  const par = arbitration.parallelism;
  const span = par ? Math.max(par.wall_ms, 1) : 1;

  return (
    <div className="agent-panel" style={{ borderColor: "color-mix(in srgb, var(--accent) 22%, var(--border))" }}>
      <div className="agent-panel__header">
        <div className="flex items-center gap-2">
          <Layers className="h-3.5 w-3.5" style={{ color: "var(--accent)" }} strokeWidth={1.9} />
          <span className="font-display text-[13px] font-semibold text-color-text">Parallel investigation</span>
          <span className="text-[11px] text-text-muted">{arbitration.host}</span>
        </div>
        {par && (
          <span
            className="agent-pill"
            style={{
              color: par.concurrent ? "var(--ok)" : "var(--warn)",
              background: par.concurrent ? "var(--ok-soft)" : "var(--warn-soft)",
            }}
            title={`${par.branch_count} branches on ${par.threads} threads`}
          >
            <Zap className="h-3 w-3" strokeWidth={2} />
            {par.concurrent
              ? `${par.peak_concurrency} at once${par.speedup ? ` · ${par.speedup}× faster` : ""}`
              : "ran sequentially"}
          </span>
        )}
      </div>

      {par && (
        <div className="flex flex-col gap-1">
          {par.branches
            .filter((b) => b.hostname === arbitration.host)
            .map((b) => {
              const meta = agentMeta(b.agent);
              const left = (b.offset_ms / span) * 100;
              const width = Math.max(2, (b.duration_ms / span) * 100);
              return (
                <div key={`${b.agent}-${b.hostname}`} className="grid grid-cols-[72px_minmax(0,1fr)_44px] items-center gap-2">
                  <span className="text-[11px] font-medium" style={{ color: meta.color }}>
                    {b.agent}
                  </span>
                  <div className="relative h-2 rounded-full" style={{ background: "var(--border-soft)" }} title={b.thread}>
                    <div
                      className="absolute top-0 h-full rounded-full"
                      style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%`, background: meta.color }}
                    />
                  </div>
                  <span className="text-right font-mono text-[10.5px] text-text-muted">{formatMs(b.duration_ms)}</span>
                </div>
              );
            })}
          <div className="text-[10.5px] text-text-muted">
            wall-clock {formatMs(par.wall_ms)} vs {formatMs(par.sequential_ms)} one after another
          </div>
        </div>
      )}

      <ul className="flex flex-col gap-1.5">
        {arbitration.theories.map((t) => {
          const v = VERDICT_STYLE[t.verdict] ?? VERDICT_STYLE.no_signal;
          const meta = agentMeta(t.agent);
          const Icon = t.verdict === "winner" ? Crown : meta.icon;
          return (
            <li
              key={t.agent}
              className="flex items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-1.5"
              style={{ background: "var(--canvas)", borderLeft: `3px solid ${v.color}` }}
            >
              <Icon className="h-3.5 w-3.5 shrink-0" style={{ color: v.color }} strokeWidth={2} />
              <span className="text-[12.5px] font-semibold text-color-text">{t.agent}</span>
              <span className="agent-pill" style={{ color: v.color, background: v.soft }}>
                {v.label}
              </span>
              <span className="ml-auto flex items-center gap-2 font-mono text-[11px] text-text-muted">
                {t.restricted ? (
                  <span>restricted</span>
                ) : (
                  <>
                    {!!t.corroboration_bonus && t.corroboration_bonus > 0 && <span>+{t.corroboration_bonus}</span>}
                    <span>{t.confidence_pct}%</span>
                  </>
                )}
              </span>
            </li>
          );
        })}
      </ul>

      <div className="text-[11.5px] leading-relaxed text-text-dim">{arbitration.why}</div>
    </div>
  );
}

function ExpertPanel({ data }: { data: AgentExpertData }) {
  return (
    <>
      {data.arbitration && <ArbitrationPanel arbitration={data.arbitration} />}
      <ExpertRunbookPanel data={data} />
    </>
  );
}

function ExpertRunbookPanel({ data }: { data: AgentExpertData }) {
  if (!data.matched_symptom_id) return <ExpertSourcesPanel data={data} />;

  return (
    <div className="agent-panel" style={{ borderColor: "color-mix(in srgb, var(--accent) 22%, var(--border))" }}>
      <div className="agent-panel__header">
        <div className="flex items-center gap-2">
          <Wrench className="h-3.5 w-3.5" style={{ color: "var(--accent)" }} strokeWidth={1.9} />
          <span className="font-display text-[13px] font-semibold text-color-text">{data.matched_symptom_title}</span>
        </div>
        {data.category && (
          <span className="agent-pill" style={{ color: "var(--accent)", background: "var(--accent-soft)" }}>
            {EXPERT_CATEGORY_LABEL[data.category] ?? data.category}
          </span>
        )}
      </div>

      {data.diagnosed_by && (
        <div
          className="flex items-start gap-2 rounded-[var(--radius-control)] px-2.5 py-2 text-[12px] leading-relaxed"
          style={{ background: "var(--canvas)", color: "var(--text-faint)" }}
        >
          <Lightbulb className="mt-[1px] h-3.5 w-3.5 shrink-0" style={{ color: "var(--text-muted)" }} strokeWidth={2} />
          <span>Walking through this after the {data.diagnosed_by} agent&apos;s finding above.</span>
        </div>
      )}

      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        <CommandSection title="Confirm it yourself" commands={data.confirm_commands ?? []} />
        <CommandSection title="Usually done about it" commands={data.remediation_commands ?? []} />
      </div>

      {data.doc_ref && (
        <div className="flex items-center gap-1.5 text-[11px] text-text-muted">
          <FileText className="h-3 w-3" strokeWidth={1.75} />
          {data.doc_ref}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Anomaly panel -- the two sub-orchestration signals (metric-check +
// log-check, see services/api/app/agents/nodes/anomaly.py) rendered as two
// evidence cards, plus the merged confidence score. Unlike the other three
// panels this one shows its work: the narrative in the answer text already
// says what was found, this panel is where you can see *why* -- the actual
// flag/reading and the actual log line(s) it was corroborated against.
// ---------------------------------------------------------------------------

function confidenceTone(confidence: number) {
  if (confidence >= 0.85) return { color: "var(--crit)", soft: "var(--crit-soft)", label: "High confidence" };
  if (confidence >= 0.5) return { color: "var(--warn)", soft: "var(--warn-soft)", label: "Possible incident" };
  return { color: "var(--ok)", soft: "var(--ok-soft)", label: "Low confidence" };
}

function AnomalyMetricCard({ signal }: { signal: AgentAnomalyData["metric_signal"] }) {
  const data = signal?.data;

  if (!signal?.has_signal || !data) {
    return (
      <div className="agent-anomaly-subcard">
        <div className="agent-anomaly-subcard__head" style={{ color: "var(--ok)" }}>
          <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={2} />
          Metric check
        </div>
        <p className="text-[12px] leading-relaxed text-text-muted">{signal?.detail ?? "No metric signal was returned."}</p>
      </div>
    );
  }

  if (data.source === "anomaly_flags") {
    return (
      <div className="agent-anomaly-subcard">
        <div className="agent-anomaly-subcard__head">
          <Gauge className="h-3.5 w-3.5" style={{ color: "var(--text-muted)" }} strokeWidth={1.9} />
          Metric check
          <span
            className="agent-pill ml-auto"
            style={{ color: SEVERITY_COLOR[data.severity], background: SEVERITY_SOFT[data.severity] }}
          >
            {SEVERITY_LABEL[data.severity]}
          </span>
        </div>
        <div className="agent-mini-stat-grid">
          <MiniStat label="Metric" value={metricLabel(data.metric_name)} />
          <MiniStat label="Current value" value={data.current_value.toFixed(1)} />
          <MiniStat label="Z-score" value={`${data.z_score.toFixed(1)}σ`} />
          <MiniStat
            label="Detected"
            value={data.detected_at ? formatRelativeTime(new Date(data.detected_at).getTime()) : "—"}
          />
        </div>
        {data.other_flagged_metrics.length > 0 && (
          <p className="text-[11px] text-text-faint">
            Also flagged: {data.other_flagged_metrics.map(metricLabel).join(", ")}
          </p>
        )}
      </div>
    );
  }

  // Live-threshold fallback tier -- reuse the same StatBar tiles the
  // monitoring panel uses, since it's the same live reading.
  return (
    <div className="agent-anomaly-subcard">
      <div className="agent-anomaly-subcard__head">
        <Gauge className="h-3.5 w-3.5" style={{ color: "var(--text-muted)" }} strokeWidth={1.9} />
        Metric check
        <span
          className="agent-pill ml-auto"
          style={{
            color: data.health === "healthy" ? "var(--ok)" : "var(--warn)",
            background: data.health === "healthy" ? "var(--ok-soft)" : "var(--warn-soft)",
          }}
        >
          live reading
        </span>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <StatBar label="CPU" value={data.cpu_percent} icon={Cpu} />
        <StatBar label="Memory" value={data.memory_percent} icon={MemoryStick} />
        <StatBar label="Disk" value={data.disk_percent} icon={HardDrive} />
      </div>
      <p className="text-[11px] text-text-faint">Not yet scored by the anomaly detector -- a live threshold read.</p>
    </div>
  );
}

function AnomalyLogCard({ signal, hostname }: { signal: AgentAnomalyData["log_signal"]; hostname: string }) {
  return (
    <div className="agent-anomaly-subcard">
      <div className="agent-anomaly-subcard__head">
        <ScrollText className="h-3.5 w-3.5" style={{ color: "var(--text-muted)" }} strokeWidth={1.9} />
        Correlated logs
        {signal.has_signal && (
          <span className="agent-pill" style={{ color: "var(--crit)", background: "var(--crit-soft)" }}>
            {signal.entries.length} found
          </span>
        )}
        <Link
          href={`/logs?host=${encodeURIComponent(hostname)}&minutes=60`}
          className="ml-auto inline-flex items-center gap-1 text-[11px] font-medium no-underline transition-colors hover:text-color-text"
          style={{ color: "var(--accent)" }}
        >
          Check all logs
          <ExternalLink className="h-3 w-3" strokeWidth={2} />
        </Link>
      </div>

      {signal.has_signal ? (
        <div>
          {signal.entries.map((entry, i) => (
            <div key={`${entry.ts}-${i}`} className="agent-anomaly-log-row">
              <Clock className="mt-[2px] h-3 w-3 shrink-0" style={{ color: "var(--text-faint)" }} strokeWidth={1.75} />
              <span className="shrink-0 whitespace-nowrap text-text-faint">{formatRelativeTime(entry.ts)}</span>
              {entry.service && (
                <span
                  className="shrink-0 rounded px-1 py-[1px] text-[10.5px] font-medium"
                  style={{ background: "var(--border-soft)", color: "var(--text-muted)" }}
                >
                  {entry.service}
                </span>
              )}
              <span className="agent-anomaly-log-row__line" title={entry.line}>
                {entry.line}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-[12px] leading-relaxed text-text-muted">{signal.detail}</p>
      )}
    </div>
  );
}

function AnomalyPanel({ data, confidence }: { data: AgentAnomalyData; confidence?: number | null }) {
  return (
    <>
      {data.arbitration && <ArbitrationPanel arbitration={data.arbitration} />}
      <AnomalyFindingPanel data={data} confidence={confidence} />
    </>
  );
}

function AnomalyFindingPanel({ data, confidence }: { data: AgentAnomalyData; confidence?: number | null }) {
  const tone = confidenceTone(confidence ?? 0);

  return (
    <div className="agent-panel" style={{ borderColor: "color-mix(in srgb, var(--crit) 22%, var(--border))" }}>
      <div className="agent-panel__header">
        <div className="flex items-center gap-2">
          <ShieldAlert className="h-3.5 w-3.5" style={{ color: "var(--crit)" }} strokeWidth={1.9} />
          <span className="font-display text-[13px] font-semibold text-color-text">{data.hostname}</span>
          <span className="text-[11px] text-text-muted">{data.role}</span>
        </div>
        {typeof confidence === "number" && (
          <span className="agent-pill" style={{ color: tone.color, background: tone.soft }}>
            {tone.label} · {Math.round(confidence * 100)}%
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        <AnomalyMetricCard signal={data.metric_signal} />
        <AnomalyLogCard signal={data.log_signal} hostname={data.hostname} />
      </div>

      {data.likely_cause && (
        <div
          className="flex items-start gap-2 rounded-[var(--radius-control)] px-2.5 py-2 text-[12px] leading-relaxed"
          style={{ background: "var(--warn-soft)", color: "var(--text-dim)" }}
        >
          <Lightbulb className="mt-[1px] h-3.5 w-3.5 shrink-0" style={{ color: "var(--warn)" }} strokeWidth={2} />
          <span>
            <span className="font-semibold" style={{ color: "var(--warn)" }}>
              Possible cause (unconfirmed):
            </span>{" "}
            {data.likely_cause}
          </span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Word-by-word reveal + matching skeleton -- the orchestrator answers in one
// shot (routers/agents.py has no token stream yet), so this fakes the feel
// of one client-side: the markdown answer types in word by word, and the
// agent-specific panel underneath sits in as a pulsing skeleton until the
// text finishes, then fades into the real stat tiles / chart / sources.
// ---------------------------------------------------------------------------

/** Splits on whitespace while keeping the whitespace as its own token (via
 * a capturing group), so `tokens.slice(0, n).join("")` reproduces the
 * original spacing exactly -- no join-separator guessing needed. */
function useTypewriter(text: string, active: boolean, onDone?: () => void) {
  const tokens = useMemo(() => text.split(/(\s+)/), [text]);
  const [count, setCount] = useState(active ? 0 : tokens.length);

  useEffect(() => {
    if (!active) return;
    if (tokens.length === 0) {
      onDone?.();
      return;
    }
    // Aim for a consistent ~1.1s total regardless of answer length, clamped
    // to a per-token pace that still reads as "typing" rather than a blur
    // on very long answers or a crawl on very short ones.
    const perToken = Math.min(28, Math.max(8, 1100 / tokens.length));
    let i = 0;
    const id = setInterval(() => {
      i += 1;
      setCount(i);
      if (i >= tokens.length) {
        clearInterval(id);
        onDone?.();
      }
    }, perToken);
    return () => clearInterval(id);
    // Only re-run when the text identity or active flag actually changes --
    // onDone is a fresh closure every render and isn't part of the timing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tokens, active]);

  return { visible: tokens.slice(0, count).join(""), finished: count >= tokens.length };
}

function SkeletonBar({ width = "100%" }: { width?: string }) {
  return (
    <div
      className="h-[10px] animate-pulse rounded-full"
      style={{ width, background: "var(--border-soft)" }}
    />
  );
}

function AgentPanelSkeleton({ agentUsed }: { agentUsed?: string }) {
  const meta = agentMeta(agentUsed);
  if (agentUsed === "rag") {
    return (
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {[110, 92, 68].map((w, i) => (
          <div
            key={i}
            className="h-[26px] animate-pulse rounded-[var(--radius-control)]"
            style={{ width: w, background: "var(--canvas)", border: "1px solid var(--border-soft)" }}
          />
        ))}
      </div>
    );
  }
  if (agentUsed === "prediction") {
    return (
      <div
        className="agent-panel"
        style={{ borderColor: "color-mix(in srgb, var(--medium) 16%, var(--border))" }}
      >
        <div className="flex items-center justify-between">
          <SkeletonBar width="38%" />
          <SkeletonBar width="20%" />
        </div>
        <div
          className="h-[200px] animate-pulse rounded-[var(--radius-control)]"
          style={{ background: "var(--canvas)" }}
        />
      </div>
    );
  }
  if (agentUsed === "monitoring") {
    return (
      <div
        className="agent-panel"
        style={{ borderColor: "color-mix(in srgb, var(--ok) 16%, var(--border))" }}
      >
        <div className="flex items-center justify-between">
          <SkeletonBar width="34%" />
          <SkeletonBar width="16%" />
        </div>
        <div className="grid grid-cols-2 gap-2">
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-[46px] animate-pulse rounded-[var(--radius-control)]"
              style={{ background: "var(--canvas)", border: "1px solid var(--border-soft)" }}
            />
          ))}
        </div>
      </div>
    );
  }
  if (agentUsed === "network") {
    return (
      <div
        className="agent-panel"
        style={{ borderColor: "color-mix(in srgb, var(--chart-6) 16%, var(--border))" }}
      >
        <div className="flex items-center justify-between">
          <SkeletonBar width="34%" />
          <SkeletonBar width="16%" />
        </div>
        <div className="flex items-center justify-center gap-2 py-1">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="h-[58px] w-[90px] animate-pulse rounded-[var(--radius-control)]"
              style={{ background: "var(--canvas)", border: "1px solid var(--border-soft)" }}
            />
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2">
          {[0, 1].map((i) => (
            <div
              key={i}
              className="h-[40px] animate-pulse rounded-[var(--radius-control)]"
              style={{ background: "var(--canvas)", border: "1px solid var(--border-soft)" }}
            />
          ))}
        </div>
      </div>
    );
  }
  if (agentUsed === "security") {
    return (
      <div
        className="agent-panel"
        style={{ borderColor: "color-mix(in srgb, var(--chart-4) 16%, var(--border))" }}
      >
        <div className="flex items-center justify-between">
          <SkeletonBar width="34%" />
          <SkeletonBar width="16%" />
        </div>
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-[54px] animate-pulse rounded-[var(--radius-control)]"
              style={{ background: "var(--canvas)", border: "1px solid var(--border-soft)" }}
            />
          ))}
        </div>
      </div>
    );
  }
  if (agentUsed === "anomaly") {
    return (
      <div
        className="agent-panel"
        style={{ borderColor: "color-mix(in srgb, var(--crit) 16%, var(--border))" }}
      >
        <div className="flex items-center justify-between">
          <SkeletonBar width="30%" />
          <SkeletonBar width="22%" />
        </div>
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
          {[0, 1].map((i) => (
            <div
              key={i}
              className="h-[110px] animate-pulse rounded-[var(--radius-control)]"
              style={{ background: "var(--canvas)", border: "1px solid var(--border-soft)" }}
            />
          ))}
        </div>
      </div>
    );
  }
  if (agentUsed === "openstack_expert") {
    return (
      <div
        className="agent-panel"
        style={{ borderColor: "color-mix(in srgb, var(--accent) 16%, var(--border))" }}
      >
        <div className="flex items-center justify-between">
          <SkeletonBar width="42%" />
          <SkeletonBar width="16%" />
        </div>
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
          {[0, 1].map((i) => (
            <div
              key={i}
              className="h-[120px] animate-pulse rounded-[var(--radius-control)]"
              style={{ background: "var(--canvas)", border: "1px solid var(--border-soft)" }}
            />
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className="reasoning-trace">
      <Loader2 className="h-3 w-3 animate-spin" style={{ color: meta.color }} strokeWidth={2} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dispatcher -- answer text (always) plus whichever panel matches agent_used.
// ---------------------------------------------------------------------------

function MonitoringSwitch({ data }: { data: AgentRawData }) {
  return isFleet(data) ? (
    <MonitoringFleetPanel data={data as AgentMonitoringFleetData} />
  ) : (
    <MonitoringPanel data={data as AgentMonitoringData} />
  );
}

function PredictionSwitch({ data }: { data: AgentRawData }) {
  return isFleet(data) ? (
    <PredictionFleetPanel data={data as AgentPredictionFleetData} />
  ) : (
    <PredictionPanel data={data as AgentPredictionData} />
  );
}

/** Static render -- full answer + panel, no animation. Used for messages
 * loaded from history (they've already "arrived"). */
export function AgentAnswerPanel({
  agentUsed,
  rawData,
  answer,
  confidence,
}: {
  agentUsed?: string;
  rawData?: AgentRawData | null;
  answer: string;
  confidence?: number | null;
}) {
  return (
    <div className="min-w-0">
      <Markdown text={answer} />
      {agentUsed === "monitoring" && rawData && <MonitoringSwitch data={rawData} />}
      {agentUsed === "network" && rawData && <NetworkPanel data={rawData as AgentNetworkData} />}
      {agentUsed === "security" && rawData && <SecurityPanel data={rawData as AgentSecurityData} />}
      {agentUsed === "prediction" && rawData && <PredictionSwitch data={rawData} />}
      {agentUsed === "rag" && rawData && <RagPanel data={rawData as AgentRagData} />}
      {agentUsed === "anomaly" && rawData && (
        <AnomalyPanel data={rawData as AgentAnomalyData} confidence={confidence} />
      )}
      {agentUsed === "openstack_expert" && rawData && <ExpertPanel data={rawData as AgentExpertData} />}
    </div>
  );
}

/** Live render -- types the answer in word by word, holding a matching
 * skeleton over the agent panel until the text finishes, then fades the
 * real panel in. `animate` is false for anything not freshly arrived
 * (history reloads, conversation switches), which skips straight to the
 * static end state via AgentAnswerPanel instead. */
export function AnimatedAgentAnswer({
  agentUsed,
  rawData,
  answer,
  animate,
  onSettled,
  confidence,
}: {
  agentUsed?: string;
  rawData?: AgentRawData | null;
  answer: string;
  animate: boolean;
  onSettled?: () => void;
  confidence?: number | null;
}) {
  // Word-by-word typing shreds tables, fenced blocks and callouts (half a
  // table is just pipes), so structured answers are shown whole.
  const rich = /^```|^\|.*\|\s*$|^> \[!/m.test(answer);
  const typing = animate && !rich;
  const { visible, finished } = useTypewriter(answer, typing, onSettled);

  useEffect(() => {
    if (animate && rich) onSettled?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!animate) {
    return <AgentAnswerPanel agentUsed={agentUsed} rawData={rawData} answer={answer} confidence={confidence} />;
  }

  const showPanel = finished && !!rawData;
  return (
    <div className="min-w-0">
      <Markdown text={visible} />
      {!showPanel && rawData && <AgentPanelSkeleton agentUsed={agentUsed} />}
      {showPanel && (
        <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
          {agentUsed === "monitoring" && <MonitoringSwitch data={rawData} />}
          {agentUsed === "network" && <NetworkPanel data={rawData as AgentNetworkData} />}
          {agentUsed === "security" && <SecurityPanel data={rawData as AgentSecurityData} />}
          {agentUsed === "prediction" && <PredictionSwitch data={rawData} />}
          {agentUsed === "rag" && <RagPanel data={rawData as AgentRagData} />}
          {agentUsed === "anomaly" && <AnomalyPanel data={rawData as AgentAnomalyData} confidence={confidence} />}
          {agentUsed === "openstack_expert" && <ExpertPanel data={rawData as AgentExpertData} />}
        </motion.div>
      )}
    </div>
  );
}
