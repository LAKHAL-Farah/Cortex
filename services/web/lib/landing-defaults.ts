// Default copy for each Landing section. Used as fallback when no override row
// exists in the `landing_sections` table.
//
// Product: Cortex -- an AI cockpit for OpenStack private clouds. Prometheus +
// node_exporter metrics and Loki logs feed median/MAD weekday-hour baselines, a
// Neo4j topology graph (the "living model") that alerts are correlated across,
// pooled quantile forecasts, security signals (auth logs, security groups,
// exposed ports, CVEs, Keystone tokens, optional eBPF), a LangGraph multi-agent
// copilot grounded in Qdrant-indexed runbooks and OpenStack docs, and a
// remediation flow that proposes, simulates, asks for approval and only then
// executes via Ansible -- Confidence-Ladder Level 0, every step audited.
//
// Keep every claim here true of the shipped code; this page is the first thing
// people read about the product.

export type LandingSectionKey =
  | "hero"
  | "popular_events"
  | "features"
  | "guardrails"
  | "cta";

export interface HeroContent {
  badge: string;
  headline_prefix: string;
  rotating_words: string[];
  subhead: string;
  cta: string;
}

export interface PopularEventsContent {
  title_line_1: string;
  title_line_2: string;
  subhead: string;
  cta_label: string;
}

export interface FeaturesContent {
  eyebrow: string;
  title_line_1: string;
  title_line_2: string;
  subhead: string;
  items: { tag: string; title: string; description: string }[];
}

export interface GuardrailsContent {
  eyebrow: string;
  title: string;
  subhead: string;
  items: { tag: string; title: string; description: string }[];
}

export interface CtaContent {
  title_line_1: string;
  title_line_2: string;
  subhead: string;
  cta_label: string;
}

export const LANDING_DEFAULTS = {
  hero: {
    badge: "AI cockpit for OpenStack",
    headline_prefix: "Operate your OpenStack cloud with",
    rotating_words: ["AI diagnosis.", "early warnings.", "approved fixes.", "full audit trails."],
    subhead:
      "Cortex collects metrics and logs from every node, learns what normal looks like on your own infrastructure, groups related alerts into one incident through a live topology graph, and explains it in plain language. It proposes the fix — you approve it.",
    cta: "Open the cockpit",
  } as HeroContent,
  popular_events: {
    title_line_1: "Every node,",
    title_line_2: "one health picture",
    subhead:
      "Controller, compute, storage, network and monitoring nodes report through node_exporter. Each card shows what Cortex surfaces for a node — health, anomalies against its baseline, capacity forecasts. Illustrative fleet; yours appears once nodes are registered.",
    cta_label: "Open your fleet",
  } as PopularEventsContent,
  features: {
    eyebrow: "Built for OpenStack operators",
    title_line_1: "From raw telemetry to",
    title_line_2: "an approved fix.",
    subhead:
      "Metrics, logs, topology and your own runbooks feed a team of specialised agents. Detection, diagnosis and remediation are one workflow, with a person on the last step.",
    items: [
      {
        tag: "Detection",
        title: "Baselines that know your Tuesday 10:00",
        description:
          "Robust median/MAD baselines per node, per metric, per weekday and hour. Deviations are scored 2 / 3 / 4σ as medium, high and critical, so normal rhythms stay quiet and real drift stands out.",
      },
      {
        tag: "Correlation",
        title: "One incident, not twenty alerts",
        description:
          "Alerts are walked across the live topology graph — hosts, services, networks, instances — and grouped into a single incident with a probable root cause and a plain-language narrative.",
      },
      {
        tag: "Forecasting & capacity",
        title: "Saturation seen in advance",
        description:
          "Pooled quantile forecasts for CPU, memory and disk give a time-to-threshold ETA. A what-if simulator, quota and budget alerts, and VM right-sizing turn it into capacity decisions.",
      },
      {
        tag: "Security",
        title: "Security signals in context",
        description:
          "SSH auth-log anomalies, security-group drift, exposed ports, CVE matching and Keystone token audit — plus kernel-level eBPF signals from Falco or Tetragon when a sensor is deployed.",
      },
      {
        tag: "Copilot",
        title: "Ask the cloud, in plain language",
        description:
          "A LangGraph orchestrator routes your question to the monitoring, anomaly, prediction, network, security and knowledge agents, with answers grounded in your runbooks and the OpenStack docs indexed in Qdrant.",
      },
      {
        tag: "Remediation",
        title: "Fixes you approve, never surprises",
        description:
          "The remediation agent proposes a concrete fix and simulates its impact on the topology graph. You approve, reject or ask for more information, then run it with an explicit click via Ansible.",
      },
    ],
  } as FeaturesContent,

  guardrails: {
    eyebrow: "Guardrails",
    title: "Built to be trusted with production",
    subhead:
      "An AI that can propose changes to your cloud needs hard limits. These are enforced in the product, not left to convention.",
    items: [
      {
        tag: "Autonomy",
        title: "Nothing runs without your click",
        description:
          "Cortex sits at Level 0 of the confidence ladder: it diagnoses and proposes, a person approves, and only an explicit click executes the fix — even for low-risk changes.",
      },
      {
        tag: "Simulation",
        title: "Impact shown before you decide",
        description:
          "Every proposal is simulated read-only against the topology graph, so you see which services, containers and instances a command would touch before you approve it.",
      },
      {
        tag: "Audit",
        title: "Every decision on the record",
        description:
          "Diagnosis, proposal, approval or rejection, and execution are written to a timestamped audit trail you can browse from the console.",
      },
      {
        tag: "Access",
        title: "Role-based by default",
        description:
          "Admin, operator and viewer roles. Operators handle low and medium-risk fixes, higher-risk ones need an admin, and viewers are read-only.",
      },
      {
        tag: "Alerting",
        title: "Routed by severity",
        description:
          "Medium alerts stay on the dashboard. High and critical alerts are emailed and escalate if nobody acknowledges them. A weekly digest summarises capacity, security and what the agents caught.",
      },
      {
        tag: "Resilience",
        title: "Degrades, never goes dark",
        description:
          "If Prometheus, Loki or the language model is unreachable, each agent and dashboard widget falls back on its own with a clear degraded state instead of blanking the page.",
      },
    ],
  } as GuardrailsContent,
  cta: {
    title_line_1: "Stop correlating alerts by hand.",
    title_line_2: "Let Cortex explain the incident.",
    subhead:
      "Register your nodes, point Cortex at Prometheus and Loki, and get baselines, correlated incidents, forecasts and reviewed fix proposals in one console.",
    cta_label: "Sign in to Cortex",
  } as CtaContent,
};

export type LandingContentMap = {
  hero: HeroContent;
  popular_events: PopularEventsContent;
  features: FeaturesContent;
  guardrails: GuardrailsContent;
  cta: CtaContent;
};

export const LANDING_SECTION_LABELS: Record<LandingSectionKey, string> = {
  hero: "Hero",
  popular_events: "Fleet status heading",
  features: "Capabilities grid",
  guardrails: "Guardrails",
  cta: "Final call to action",
};
