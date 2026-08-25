"use client";

import { useRef } from "react";
import Link from "next/link";
import { motion, type Variants } from "framer-motion";
import {
  Gauge,
  CalendarClock,
  Waypoints,
  Bot,
  Workflow,
  ShieldCheck,
  Wallet,
  ArrowRight,
  Check,
  X,
} from "lucide-react";
import ThemeToggle from "@/components/ThemeToggle";
import HexTopologyHero from "./HexTopologyHero";

/** Sets --mx/--my on a .landing-glass element from the pointer position, so
 * the card's cursor-tracked highlight (globals.css) follows the mouse. Same
 * technique as the rest of the app's hover states, just parameterized per
 * pointer event instead of a fixed CSS :hover. */
function trackCursor(e: React.MouseEvent<HTMLElement>) {
  const el = e.currentTarget;
  const r = el.getBoundingClientRect();
  el.style.setProperty("--mx", `${((e.clientX - r.left) / r.width) * 100}%`);
  el.style.setProperty("--my", `${((e.clientY - r.top) / r.height) * 100}%`);
}

const fadeUp: Variants = {
  hidden: { opacity: 0, y: 24 },
  show: { opacity: 1, y: 0, transition: { duration: 0.6, ease: [0.16, 1, 0.3, 1] } },
};

const TICKER: { label: string; value: string; tone?: "ok" | "warn" }[] = [
  { label: "Nodes monitored", value: "1,240", tone: "ok" },
  { label: "Open anomalies", value: "3", tone: "warn" },
  { label: "MTTR reduction", value: "−61%", tone: "ok" },
  { label: "Forecast horizon", value: "90 days" },
  { label: "Agents online", value: "4 / 4", tone: "ok" },
  { label: "Pending approvals", value: "1", tone: "warn" },
  { label: "Last remediation", value: "2h ago" },
  { label: "Cost variance", value: "−4.2%", tone: "ok" },
];

const CAPABILITIES = [
  {
    href: "/metrics",
    icon: Gauge,
    color: "var(--role-monitoring)",
    title: "Live node monitoring",
    copy: "CPU, RAM, disk and network per node, refreshed from Prometheus and Loki — never a mock number.",
    span: "lg:col-span-3 lg:row-span-2",
  },
  {
    href: "/forecast",
    icon: CalendarClock,
    color: "var(--chart-4)",
    title: "Predictive forecasting",
    copy: "Capacity projected 24 hours to 90 days out. The confidence band widens honestly the further out you look.",
    span: "lg:col-span-3 lg:row-span-2",
  },
  {
    href: "/topology",
    icon: Waypoints,
    color: "var(--role-controller)",
    title: "Correlated root cause",
    copy: "Two related alerts become one incident narrative, traced across the real topology graph.",
    span: "lg:col-span-2",
  },
  {
    href: "/copilot",
    icon: Bot,
    color: "var(--accent)",
    title: "One chat. Every specialist.",
    copy: "The orchestrator routes plain-language questions to Monitoring, Prediction, Security or Network agents — in parallel.",
    span: "lg:col-span-4 lg:row-span-2",
  },
  {
    href: "/copilot",
    icon: Workflow,
    color: "var(--role-storage)",
    title: "Remediation, proposed",
    copy: "Simulated impact shown before anything runs. Nothing executes without a human click.",
    span: "lg:col-span-2",
  },
  {
    href: "/alerts",
    icon: ShieldCheck,
    color: "var(--crit)",
    title: "Security-group diff",
    copy: "What changed, and whether it was expected — flagged the moment it happens.",
    span: "lg:col-span-3",
  },
  {
    href: "/quotas",
    icon: Wallet,
    color: "var(--role-compute)",
    title: "Reporting & showback",
    copy: "Weekly digest and per-project cost reports, generated on schedule — not requested.",
    span: "lg:col-span-3",
  },
] as const;

const FLOW = [
  { n: "01", title: "Detect", copy: "Contextual anomaly scoring against your own baseline, not a flat threshold." },
  { n: "02", title: "Correlate", copy: "Related signals linked across the topology graph into one narrative." },
  { n: "03", title: "Explain", copy: "Cortex states why it's investigating a certain way — no black box." },
  { n: "04", title: "Propose", copy: "A specialist agent drafts a fix and simulates its impact first." },
  { n: "05", title: "Approve", copy: "A human validates. Every decision is logged and attributable." },
  { n: "06", title: "Execute", copy: "Runs via Ansible or the OpenStack SDK — only after that click." },
  { n: "07", title: "Report", copy: "Shows up in the weekly digest and the showback report automatically." },
];

export default function LandingPage() {
  const heroRef = useRef<HTMLDivElement>(null);

  return (
    <div className="landing-canvas min-h-screen">
      {/* ---------------- Nav ---------------- */}
      <header className="sticky top-4 z-50 mx-auto flex max-w-[1160px] justify-center px-4">
        <nav
          className="landing-glass flex w-full items-center justify-between gap-6 px-4 py-2.5"
          onMouseMove={trackCursor}
          aria-label="Primary"
        >
          <Link href="/" className="flex items-center gap-2">
            <div
              className="flex h-7 w-7 items-center justify-center rounded-[7px] text-sm font-semibold text-white"
              style={{ background: "var(--accent)" }}
            >
              C
            </div>
            <span className="font-display text-[15px] font-semibold text-color-text">Cortex</span>
          </Link>

          <div className="hidden items-center gap-6 text-sm text-text-dim sm:flex">
            <a href="#capabilities" className="hover:text-color-text">Capabilities</a>
            <a href="#flow" className="hover:text-color-text">How it works</a>
            <a href="#agents" className="hover:text-color-text">Agents</a>
          </div>

          <div className="flex items-center gap-2">
            <ThemeToggle />
            <Link
              href="/dashboard"
              className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3.5 py-2 text-sm font-medium text-white transition-transform hover:scale-[1.03]"
              style={{ background: "var(--accent)" }}
            >
              Open the cockpit
              <ArrowRight className="h-3.5 w-3.5" strokeWidth={2} />
            </Link>
          </div>
        </nav>
      </header>

      {/* ---------------- Hero ---------------- */}
      <section className="mx-auto max-w-[1160px] px-4 pb-10 pt-20 sm:pt-28">
        <div className="grid items-center gap-10 lg:grid-cols-[1.05fr_0.95fr] lg:gap-14">
          <motion.div initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.4 }} variants={fadeUp}>
            <div className="eyebrow flex items-center gap-2" style={{ color: "var(--accent)" }}>
              <span className="status-dot glow-pulse" style={{ background: "var(--accent)", ["--pulse-color" as string]: "var(--accent)" }} />
              Autonomous infrastructure intelligence
            </div>
            <h1 className="font-display mt-4 text-[40px] font-semibold leading-[1.05] tracking-tight text-color-text sm:text-[52px]">
              The nervous system<br />for your infrastructure.
            </h1>
            <p className="mt-5 max-w-md text-[17px] leading-relaxed text-text-faint">
              Cortex watches every OpenStack node, correlates the signal across metrics, logs and topology, and
              proposes the fix in plain language — before your team opens a ticket.
            </p>

            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link
                href="/dashboard"
                className="inline-flex items-center gap-2 rounded-full px-6 py-3.5 text-sm font-semibold text-white transition-transform hover:scale-[1.03]"
                style={{ background: "var(--accent)" }}
              >
                Open the cockpit
                <ArrowRight className="h-4 w-4" strokeWidth={2} />
              </Link>
              <a
                href="#flow"
                className="inline-flex items-center gap-2 rounded-full px-6 py-3.5 text-sm font-semibold text-color-text transition-colors hover:bg-[var(--canvas)]"
                style={{ border: "1px solid var(--border)" }}
              >
                See how it decides
              </a>
            </div>

            <div className="mt-9 flex flex-wrap gap-x-8 gap-y-3">
              {[
                ["Nodes monitored", "1,240+"],
                ["Mean time to explain", "< 40s"],
                ["Executes without approval", "never"],
              ].map(([label, value]) => (
                <div key={label}>
                  <div className="stat-figure text-[15px] text-color-text">{value}</div>
                  <div className="text-xs text-text-muted">{label}</div>
                </div>
              ))}
            </div>
          </motion.div>

          <motion.div
            ref={heroRef}
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, amount: 0.3 }}
            variants={fadeUp}
            transition={{ delay: 0.15 }}
            className="landing-glass relative h-[420px] p-5"
            onMouseMove={trackCursor}
          >
            <div className="eyebrow">Live topology · openstack-prod</div>
            <div className="mt-1 flex items-center gap-1.5 text-xs" style={{ color: "var(--ok)" }}>
              <span className="status-dot glow-pulse" style={{ background: "var(--ok)", ["--pulse-color" as string]: "var(--ok)" }} />
              analyzing 6 nodes
            </div>
            <div className="h-[330px]">
              <HexTopologyHero />
            </div>
          </motion.div>
        </div>
      </section>

      {/* ---------------- Ticker ---------------- */}
      <div className="overflow-hidden border-y py-3.5" style={{ borderColor: "var(--border-soft)" }}>
        <div className="landing-ticker-track flex w-max gap-12">
          {[...TICKER, ...TICKER].map((item, i) => (
            <div key={i} className="flex items-center gap-2 whitespace-nowrap text-[13px] text-text-faint">
              {item.label}
              <b
                className="stat-figure text-color-text"
                style={{ color: item.tone === "ok" ? "var(--ok)" : item.tone === "warn" ? "var(--warn)" : undefined }}
              >
                {item.value}
              </b>
            </div>
          ))}
        </div>
      </div>

      {/* ---------------- Capabilities bento ---------------- */}
      <section id="capabilities" className="mx-auto max-w-[1160px] px-4 py-20 sm:py-28">
        <motion.div
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, amount: 0.3 }}
          variants={fadeUp}
          className="mb-12 max-w-xl"
        >
          <div className="eyebrow" style={{ color: "var(--accent)" }}>What Cortex sees</div>
          <h2 className="font-display mt-3 text-[32px] font-semibold tracking-tight text-color-text sm:text-[38px]">
            One model of your infrastructure. Not fifty dashboards.
          </h2>
          <p className="mt-3 text-[15px] leading-relaxed text-text-faint">
            Every card below opens the real page — the same data your on-call team looks at, correlated instead of scattered.
          </p>
        </motion.div>

        <div className="grid gap-4 lg:grid-cols-6 lg:auto-rows-[180px]">
          {CAPABILITIES.map((c, i) => (
            <motion.div
              key={c.title}
              initial="hidden"
              whileInView="show"
              viewport={{ once: true, amount: 0.3 }}
              variants={fadeUp}
              transition={{ delay: (i % 3) * 0.08 }}
              className={c.span}
            >
              <Link
                href={c.href}
                onMouseMove={trackCursor}
                className="landing-glass group flex h-full flex-col justify-between p-5 transition-transform hover:-translate-y-1"
              >
                <div>
                  <span
                    className="mb-3 inline-flex h-9 w-9 items-center justify-center rounded-[var(--radius-control)]"
                    style={{ background: `color-mix(in srgb, ${c.color} 14%, transparent)` }}
                  >
                    <c.icon className="h-[18px] w-[18px]" style={{ color: c.color }} strokeWidth={1.75} />
                  </span>
                  <h3 className="font-display text-[17px] font-semibold text-color-text">{c.title}</h3>
                  <p className="mt-1.5 text-[13.5px] leading-relaxed text-text-faint">{c.copy}</p>
                </div>

                {c.title === "One chat. Every specialist." && <AgentChatPreview />}
                {c.title === "Remediation, proposed" && <RemediationPreview />}
                {c.title === "Security-group diff" && <SecurityDiffPreview />}
                {c.title === "Reporting & showback" && <ReportTagsPreview />}

                <div
                  className="mt-3 inline-flex items-center gap-1 text-xs font-medium opacity-0 transition-opacity group-hover:opacity-100"
                  style={{ color: c.color }}
                >
                  Open {c.href} <ArrowRight className="h-3 w-3" strokeWidth={2.5} />
                </div>
              </Link>
            </motion.div>
          ))}
        </div>
      </section>

      {/* ---------------- Flow ---------------- */}
      <section id="flow" className="mx-auto max-w-[1160px] px-4 pb-20 sm:pb-28">
        <motion.div initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.3 }} variants={fadeUp} className="mb-10 max-w-xl">
          <div className="eyebrow" style={{ color: "var(--accent)" }}>From signal to resolution</div>
          <h2 className="font-display mt-3 text-[32px] font-semibold tracking-tight text-color-text sm:text-[38px]">
            Every incident follows the same chain.
          </h2>
          <p className="mt-3 text-[15px] leading-relaxed text-text-faint">
            Detection to execution, fully logged — so approving a fix is never a leap of faith.
          </p>
        </motion.div>

        <motion.div
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, amount: 0.2 }}
          variants={fadeUp}
          onMouseMove={trackCursor}
          className="landing-glass grid gap-6 overflow-x-auto p-6 sm:grid-cols-4 lg:grid-cols-7"
        >
          {FLOW.map((step) => (
            <div key={step.n} className="min-w-[130px]">
              <div className="stat-figure text-xs" style={{ color: "var(--text-muted)" }}>{step.n}</div>
              <h4 className="font-display mt-2 text-[15px] font-semibold text-color-text">{step.title}</h4>
              <p className="mt-1 text-[12.5px] leading-relaxed text-text-faint">{step.copy}</p>
            </div>
          ))}
        </motion.div>
      </section>

      {/* ---------------- CTA ---------------- */}
      <section className="mx-auto max-w-[1160px] px-4 pb-24">
        <motion.div
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, amount: 0.4 }}
          variants={fadeUp}
          className="landing-glass glow-surface flex flex-col items-center px-8 py-16 text-center"
        >
          <div className="eyebrow" style={{ color: "var(--accent)" }}>Ready when you are</div>
          <h2 className="font-display mt-3 max-w-lg text-[30px] font-semibold tracking-tight text-color-text sm:text-[36px]">
            Give your infrastructure a mind of its own.
          </h2>
          <p className="mt-3 max-w-md text-[15px] leading-relaxed text-text-faint">
            Cortex is running against real OpenStack clusters today. Open the cockpit to see the current sprint&rsquo;s build.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Link
              href="/dashboard"
              className="inline-flex items-center gap-2 rounded-full px-6 py-3.5 text-sm font-semibold text-white transition-transform hover:scale-[1.03]"
              style={{ background: "var(--accent)" }}
            >
              Open the cockpit
              <ArrowRight className="h-4 w-4" strokeWidth={2} />
            </Link>
            <Link
              href="/copilot"
              className="inline-flex items-center gap-2 rounded-full px-6 py-3.5 text-sm font-semibold text-color-text transition-colors hover:bg-[var(--canvas)]"
              style={{ border: "1px solid var(--border)" }}
            >
              Ask the copilot
            </Link>
          </div>
        </motion.div>
      </section>

      {/* ---------------- Footer ---------------- */}
      <footer className="border-t px-4 py-8" style={{ borderColor: "var(--border-soft)" }}>
        <div className="mx-auto flex max-w-[1160px] flex-col items-center justify-between gap-4 text-xs text-text-muted sm:flex-row">
          <div className="stat-figure">Cortex — infrastructure intelligence for OpenStack</div>
          <div className="flex gap-5">
            <a href="#capabilities" className="hover:text-text-dim">Capabilities</a>
            <a href="#flow" className="hover:text-text-dim">How it works</a>
            <Link href="/dashboard" className="hover:text-text-dim">Cockpit</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}

function AgentChatPreview() {
  return (
    <div className="mt-4 space-y-2">
      <div
        className="ml-auto max-w-[80%] rounded-2xl px-3 py-1.5 text-[12.5px]"
        style={{ background: "var(--accent-soft)", color: "var(--text)" }}
      >
        Why did node-07 spike at 3am?
      </div>
      <div className="max-w-[85%] rounded-2xl px-3 py-1.5 text-[12.5px]" style={{ background: "var(--canvas)", color: "var(--text-dim)" }}>
        <span className="block font-mono text-[10px]" style={{ color: "var(--role-monitoring)" }}>MONITORING AGENT</span>
        CPU spiked to 92% at 03:14, outside its Sunday baseline.
      </div>
      <div className="max-w-[85%] rounded-2xl px-3 py-1.5 text-[12.5px]" style={{ background: "var(--canvas)", color: "var(--text-dim)" }}>
        <span className="block font-mono text-[10px]" style={{ color: "var(--crit)" }}>SECURITY AGENT</span>
        Correlated with 40 failed SSH attempts from one IP.
      </div>
    </div>
  );
}

function RemediationPreview() {
  return (
    <div className="mt-3 flex gap-2">
      <span
        className="inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs font-medium"
        style={{ background: "var(--ok-soft)", color: "var(--ok)" }}
      >
        <Check className="h-3 w-3" strokeWidth={2.5} /> Approve
      </span>
      <span
        className="inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs font-medium"
        style={{ background: "var(--canvas)", color: "var(--text-faint)", border: "1px solid var(--border)" }}
      >
        <X className="h-3 w-3" strokeWidth={2.5} /> Reject
      </span>
    </div>
  );
}

function SecurityDiffPreview() {
  return (
    <div className="mt-3 font-mono text-[11.5px]">
      <div className="flex justify-between border-b py-1" style={{ borderColor: "var(--border-soft)" }}>
        <span style={{ color: "var(--crit)", textDecoration: "line-through", opacity: 0.75 }}>ingress 0.0.0.0/0 :22</span>
        <span style={{ color: "var(--ok)" }}>ingress 10.0.4.0/24 :22</span>
      </div>
      <div className="flex justify-between py-1 text-text-muted">
        <span>egress *</span>
        <span>unchanged</span>
      </div>
    </div>
  );
}

function ReportTagsPreview() {
  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      {["exec.pdf", "showback.pdf", "technical.pdf"].map((tag) => (
        <span
          key={tag}
          className="rounded-full px-2.5 py-1 font-mono text-[10.5px]"
          style={{ background: "var(--accent-soft)", color: "var(--accent)", border: "1px solid color-mix(in srgb, var(--accent) 30%, transparent)" }}
        >
          {tag}
        </span>
      ))}
    </div>
  );
}
