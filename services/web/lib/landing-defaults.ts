// Default copy for each Landing section. Used as fallback when no override row
// exists in the `landing_sections` table.
//
// Product: Cortex — an AIOps observability platform for OpenStack private
// clouds. Metrics/logs from Prometheus + Loki, median/MAD baselines, a live
// topology graph, graph-correlated incidents, quantile forecasting, and a
// RAG knowledge copilot.

export type LandingSectionKey =
  | "hero"
  | "popular_events"
  | "features"
  | "testimonials"
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

export interface TestimonialsContent {
  title: string;
  items: { quote: string; name: string; role: string }[];
}

export interface CtaContent {
  title_line_1: string;
  title_line_2: string;
  subhead: string;
  cta_label: string;
}

export const LANDING_DEFAULTS = {
  hero: {
    badge: "AIOps for private cloud",
    headline_prefix: "Your OpenStack cloud, explained by an",
    rotating_words: ["AIOps brain.", "incident copilot.", "anomaly engine.", "topology graph."],
    subhead:
      "Cortex ingests Prometheus metrics and Loki logs, learns each service's normal behaviour, correlates anomalies across your topology graph, and explains the incident in plain language — before your users notice.",
    cta: "Deploy Cortex",
  } as HeroContent,
  popular_events: {
    title_line_1: "Live signal",
    title_line_2: "across the fleet",
    subhead:
      "Controller, compute and storage nodes streaming metrics, logs and health checks into Cortex right now.",
    cta_label: "Open the console",
  } as PopularEventsContent,
  features: {
    eyebrow: "Built for cloud operators",
    title_line_1: "From raw telemetry to",
    title_line_2: "a diagnosed incident.",
    subhead:
      "Cortex closes the loop between detection, correlation, prediction and remediation on OpenStack 2024.1 deployments.",
    items: [
      {
        tag: "Detection",
        title: "Baselines, not static thresholds",
        description:
          "Median/MAD baselines per service and per node flag genuine deviations while ignoring the noise thresholds always trip on.",
      },
      {
        tag: "Correlation",
        title: "Graph-correlated incidents",
        description:
          "Alerts are walked across the topology graph so twenty symptoms collapse into one incident with a probable root cause.",
      },
      {
        tag: "Forecasting",
        title: "Saturation seen in advance",
        description:
          "Pooled quantile forecasts project CPU, memory and volume growth so you resize capacity days before it becomes an outage.",
      },
      {
        tag: "Copilot",
        title: "RAG knowledge assistant",
        description:
          "Runbooks, logs and OpenStack docs are vector-indexed so operators can ask what broke and what to run next, in plain language.",
      },
    ],
  } as FeaturesContent,

  testimonials: {
    title: "Trusted by platform teams",
    items: [
      {
        quote:
          "Cortex tied a Neutron L3 flap to a single compute host in seconds. That used to be a two-hour Grafana crawl.",
        name: "Sarah Chen",
        role: "Platform SRE",
      },
      {
        quote:
          "The MAD baselines killed our alert fatigue. Pager volume dropped by more than half in the first sprint.",
        name: "Marcus Williams",
        role: "Cloud operations lead",
      },
      {
        quote:
          "Quantile forecasting warned us about Cinder saturation four days out. We resized during business hours.",
        name: "Priya Patel",
        role: "Infrastructure engineer",
      },
      {
        quote:
          "The topology graph is the first view that actually matches how our Kolla-Ansible deployment is wired.",
        name: "James Liu",
        role: "OpenStack administrator",
      },
      {
        quote:
          "New on-call engineers ask the copilot instead of pinging me at 3am. That alone paid for the rollout.",
        name: "Amara Osei",
        role: "Head of infrastructure",
      },
    ],
  } as TestimonialsContent,
  cta: {
    title_line_1: "Stop chasing dashboards.",
    title_line_2: "Start reading root causes.",
    subhead:
      "Point Cortex at your Prometheus and Loki endpoints and get baselines, correlated incidents and forecasts on day one.",
    cta_label: "Get started free",
  } as CtaContent,
};

export type LandingContentMap = {
  hero: HeroContent;
  popular_events: PopularEventsContent;
  features: FeaturesContent;
  testimonials: TestimonialsContent;
  cta: CtaContent;
};

export const LANDING_SECTION_LABELS: Record<LandingSectionKey, string> = {
  hero: "Hero",
  popular_events: "Fleet status heading",
  features: "Capabilities grid",
  testimonials: "Testimonials",
  cta: "Final call to action",
};
