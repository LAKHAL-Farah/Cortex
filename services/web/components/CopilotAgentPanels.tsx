"use client";

import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  Clock,
  Cpu,
  FileText,
  Gauge,
  HardDrive,
  Loader2,
  MemoryStick,
  Minus,
  Sparkles,
  TrendingDown,
  TrendingUp,
  XCircle,
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
  AgentMonitoringData,
  AgentName,
  AgentPredictionData,
  AgentRagData,
  AgentRawData,
  ForecastPoint,
} from "@/lib/types";

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
}: {
  active: boolean;
  agentUsed?: string;
  elapsedMs?: number;
}) {
  const [step, setStep] = useState(0);

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
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.2 }}
      className="reasoning-trace reasoning-trace--done"
    >
      <Icon className="h-3 w-3 shrink-0" style={{ color: meta.color }} strokeWidth={2} />
      <span>
        Routed to the <span style={{ color: meta.color, fontWeight: 600 }}>{meta.label.toLowerCase()}</span>
        {typeof elapsedMs === "number" && elapsedMs > 0 ? ` · ${(elapsedMs / 1000).toFixed(1)}s` : ""}
      </span>
    </motion.div>
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

export function Markdown({ text }: { text: string }) {
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

function MiniStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="agent-mini-stat">
      <div className="agent-mini-stat__label">{label}</div>
      <div className="agent-mini-stat__value">{value}</div>
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
  return (
    <div className="reasoning-trace">
      <Loader2 className="h-3 w-3 animate-spin" style={{ color: meta.color }} strokeWidth={2} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dispatcher -- answer text (always) plus whichever panel matches agent_used.
// ---------------------------------------------------------------------------

/** Static render -- full answer + panel, no animation. Used for messages
 * loaded from history (they've already "arrived"). */
export function AgentAnswerPanel({
  agentUsed,
  rawData,
  answer,
}: {
  agentUsed?: string;
  rawData?: AgentRawData | null;
  answer: string;
}) {
  return (
    <div className="min-w-0">
      <Markdown text={answer} />
      {agentUsed === "monitoring" && rawData && <MonitoringPanel data={rawData as AgentMonitoringData} />}
      {agentUsed === "prediction" && rawData && <PredictionPanel data={rawData as AgentPredictionData} />}
      {agentUsed === "rag" && rawData && <RagPanel data={rawData as AgentRagData} />}
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
}: {
  agentUsed?: string;
  rawData?: AgentRawData | null;
  answer: string;
  animate: boolean;
  onSettled?: () => void;
}) {
  const { visible, finished } = useTypewriter(answer, animate, onSettled);

  if (!animate) {
    return <AgentAnswerPanel agentUsed={agentUsed} rawData={rawData} answer={answer} />;
  }

  const showPanel = finished && !!rawData;
  return (
    <div className="min-w-0">
      <Markdown text={visible} />
      {!showPanel && rawData && <AgentPanelSkeleton agentUsed={agentUsed} />}
      {showPanel && (
        <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
          {agentUsed === "monitoring" && <MonitoringPanel data={rawData as AgentMonitoringData} />}
          {agentUsed === "prediction" && <PredictionPanel data={rawData as AgentPredictionData} />}
          {agentUsed === "rag" && <RagPanel data={rawData as AgentRagData} />}
        </motion.div>
      )}
    </div>
  );
}
