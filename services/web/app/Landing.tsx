"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { LANDING_DEFAULTS } from "@/lib/landing-defaults";
import { Logo } from "@/components/Logo";
import ThemeToggle from "@/components/ThemeToggle";
import { TopologyGlobe } from "@/components/landing/TopologyGlobe";
import { MoleculeField } from "@/components/landing/MoleculeField";
import { MarketingButton as Button } from "@/components/MarketingButton";
import { motion, AnimatePresence, useScroll, useTransform, useReducedMotion } from "framer-motion";
import styles from "./public-pages.module.css";
import {
  ArrowRight,
  Activity,
  Share2,
  TrendingUp,
  Sparkles,
  Cpu,
  Server,
  HardDrive,
  Network,
  ShieldCheck,
  Wrench,
  MousePointerClick,
  FlaskConical,
  ScrollText,
  KeyRound,
  Bell,
  LifeBuoy,
} from "lucide-react";

// Order matches LANDING_DEFAULTS.features.items:
// detection, correlation, forecasting, security, copilot, remediation.
const FEATURE_ICONS = [Activity, Share2, TrendingUp, ShieldCheck, Sparkles, Wrench];
const FEATURE_TINTS = [
  "bg-primary/10 text-primary",
  "bg-info/10 text-info",
  "bg-success/10 text-success",
  "bg-warning/10 text-warning",
  "bg-violet/10 text-violet",
  "bg-primary/10 text-primary",
];
// Order matches LANDING_DEFAULTS.guardrails.items:
// autonomy, simulation, audit, access, alerting, resilience.
const GUARDRAIL_ICONS = [MousePointerClick, FlaskConical, ScrollText, KeyRound, Bell, LifeBuoy];
const GUARDRAIL_TINTS = [
  "bg-primary/10 text-primary",
  "bg-violet/10 text-violet",
  "bg-info/10 text-info",
  "bg-warning/10 text-warning",
  "bg-primary/10 text-primary",
  "bg-success/10 text-success",
];

const fadeUp = {
  initial: { opacity: 0, y: 20 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, amount: 0.2 },
};

/** Sparkline used in the live telemetry cards. */
function Sparkline({ color = "hsl(var(--primary))" }: { color?: string }) {
  return (
    <svg viewBox="0 0 120 34" className="w-full h-8" preserveAspectRatio="none" aria-hidden="true">
      <polyline
        points="0,28 15,24 30,26 45,17 60,20 75,11 90,14 105,6 120,3"
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <polyline
        points="0,28 15,24 30,26 45,17 60,20 75,11 90,14 105,6 120,3 120,34 0,34"
        fill={color}
        opacity="0.1"
        stroke="none"
      />
    </svg>
  );
}

const Landing = () => {
  const router = useRouter();
  const { user, loading: authLoading } = useCurrentUser();

  // Authenticated users skip the marketing page and land in the Cortex console.
  useEffect(() => {
    if (!authLoading && user) router.replace("/dashboard");
  }, [user, authLoading, router]);

  const hero = LANDING_DEFAULTS.hero;
  const fleet = LANDING_DEFAULTS.popular_events;
  const featuresContent = LANDING_DEFAULTS.features;
  const guardrailsContent = LANDING_DEFAULTS.guardrails;
  const ctaContent = LANDING_DEFAULTS.cta;
  const rotatingWords = hero.rotating_words;

  const [wordIndex, setWordIndex] = useState(0);

  useEffect(() => {
    if (!rotatingWords.length) return;
    const interval = setInterval(() => {
      setWordIndex((prev) => (prev + 1) % rotatingWords.length);
    }, 3000);
    return () => clearInterval(interval);
  }, [rotatingWords]);

  // Parallax — cover art, blueprint grids, globe and floating glass cards track scroll.
  const heroRef = useRef<HTMLElement>(null);
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll({ target: heroRef, offset: ["start start", "end start"] });
  const bgY = useTransform(scrollYProgress, [0, 1], ["0%", "18%"]);
  const bgScale = useTransform(scrollYProgress, [0, 1], [1.05, 1.18]);
  const gridY = useTransform(scrollYProgress, [0, 1], ["0%", "30%"]);
  const globeY = useTransform(scrollYProgress, [0, 1], [0, -70]);
  const globeScale = useTransform(scrollYProgress, [0, 1], [1, 1.08]);
  const cardAY = useTransform(scrollYProgress, [0, 1], [0, -40]);
  const cardBY = useTransform(scrollYProgress, [0, 1], [0, -28]);

  if (!authLoading && user) return null;

  const fleetNodes = [
    {
      icon: Server,
      name: "controller-01",
      role: "Keystone · Neutron L3 · Glance · MariaDB",
      state: "Healthy",
      tone: "success" as const,
      metric: "cpu 34%",
    },
    {
      icon: Cpu,
      name: "compute-01",
      role: "nova-compute · RabbitMQ · libvirt",
      state: "Healthy",
      tone: "success" as const,
      metric: "cpu 51%",
    },
    {
      icon: Cpu,
      name: "compute-02",
      role: "nova-compute · ovs-br-ex",
      state: "Anomaly",
      tone: "primary" as const,
      metric: "iowait 4.2σ",
    },
    {
      icon: HardDrive,
      name: "storage-01",
      role: "cinder-volume · node-exporter",
      state: "Forecast",
      tone: "warning" as const,
      metric: "88% in 4d",
    },
  ];

  const toneClass = {
    success: "bg-success/10 text-success border-success/20",
    primary: "bg-primary/10 text-primary border-primary/20",
    warning: "bg-warning/10 text-warning border-warning/20",
  };
  const dotClass = { success: "bg-success", primary: "bg-primary", warning: "bg-warning" };
  const iconClass = { success: "text-success", primary: "text-primary", warning: "text-warning" };

  return (
    <div className={`${styles.landing} min-h-screen bg-background overflow-x-hidden`}>
      {/* Page-wide artistic layer: point dust + logo-like molecules that lean
          toward the cursor. Landing route only. */}
      <MoleculeField className="fixed inset-0 z-0 h-full w-full" />
      <div aria-hidden="true" className="fixed inset-0 z-0 pointer-events-none overflow-hidden">
        <span className="aurora-orb w-[42vw] h-[42vw] -left-[10vw] top-[8vh] bg-primary/25" />
        <span
          className="aurora-orb w-[38vw] h-[38vw] right-[-8vw] top-[46vh] bg-info/25"
          style={{ animationDelay: "-6s" }}
        />
        <span
          className="aurora-orb w-[30vw] h-[30vw] left-[28vw] bottom-[-6vh] bg-violet/20"
          style={{ animationDelay: "-11s" }}
        />
      </div>

      {/* Top bar */}
      <header className={`${styles.landingTopbar} fixed top-0 inset-x-0 z-50`}>
        <div className="max-w-7xl mx-auto flex items-center justify-between h-16 px-6 lg:px-8">
          <Link href="/" aria-label="Cortex home">
            <Logo size="md" />
          </Link>
          <nav className="hidden md:flex items-center gap-7 text-sm font-medium text-muted-foreground">
            <a href="#fleet" className="hover:text-foreground transition-colors">Fleet</a>
            <a href="#features" className="hover:text-foreground transition-colors">Platform</a>
            <a href="#guardrails" className="hover:text-foreground transition-colors">Guardrails</a>
          </nav>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <Button variant="ghost" className="text-sm font-medium" asChild>
              <Link href="/login">Log in</Link>
            </Button>
          </div>
        </div>
      </header>

      {/* Hero — blueprint stage, parallax cover art, 3D topology globe */}
      <section ref={heroRef} className="relative z-10 pt-32 lg:pt-40 pb-16 lg:pb-24 overflow-hidden">
        {/* Layer 1: cover photograph, parallaxed and tinted into the palette */}
        <motion.div
          aria-hidden="true"
          className="absolute inset-x-0 -top-24 h-[130%] pointer-events-none"
          style={reduce ? undefined : { y: bgY, scale: bgScale }}
        >
          <Image
            src="/landing/hero-bg.jpg"
            alt=""
            fill
            priority
            className="w-full h-full object-cover opacity-[0.2] dark:opacity-[0.28] saturate-[0.8]"
          />
          <div
            className="absolute inset-0"
            style={{
              background:
                "radial-gradient(ellipse at 50% 0%, hsl(var(--primary) / 0.18), transparent 55%), radial-gradient(ellipse at 85% 30%, hsl(var(--info) / 0.16), transparent 50%), linear-gradient(to bottom, hsl(var(--background) / 0.6) 0%, hsl(var(--background) / 0.74) 55%, hsl(var(--background) / 0.86) 100%)",
            }}
          />
        </motion.div>

        {/* Layer 2: blueprint grids */}
        <div aria-hidden="true" className="absolute inset-0 blueprint-grid opacity-70 pointer-events-none" />
        <motion.div
          aria-hidden="true"
          className="absolute inset-0 blueprint-grid-fine opacity-40 pointer-events-none"
          style={reduce ? undefined : { y: gridY }}
        />
        <div
          aria-hidden="true"
          className="absolute inset-x-0 bottom-0 h-40 pointer-events-none"
          style={{ background: "linear-gradient(to bottom, transparent, hsl(var(--background) / 0.85))" }}
        />

        <div className="relative max-w-7xl mx-auto px-6 lg:px-8">
          <motion.div
            className="text-center max-w-3xl mx-auto"
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="inline-flex items-center gap-2 mb-6 glass px-3.5 py-1.5 rounded-full">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full rounded-full bg-success opacity-75 animate-ping" />
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-success" />
              </span>
              <span className="eyebrow">{hero.badge}</span>
            </div>
            <h1 className="text-4xl sm:text-5xl lg:text-[64px] font-display font-bold tracking-[-0.03em] leading-[1.02] text-foreground mb-6">
              {hero.headline_prefix}{" "}
              <span className="inline-block relative align-baseline">
                <AnimatePresence mode="wait">
                  <motion.span
                    key={rotatingWords[wordIndex]}
                    initial={{ opacity: 0, y: 14 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -14 }}
                    transition={{ duration: 0.3 }}
                    className="text-primary inline-block"
                  >
                    {rotatingWords[wordIndex]}
                  </motion.span>
                </AnimatePresence>
                <span className="invisible inline-block h-0 overflow-hidden" aria-hidden="true">
                  {rotatingWords.reduce((a, b) => (a.length >= b.length ? a : b), "")}
                </span>
              </span>
            </h1>
            <p className="text-base lg:text-lg text-muted-foreground max-w-xl mx-auto mb-9 leading-relaxed">
              {hero.subhead}
            </p>
            <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
              <Button size="lg" className="text-sm font-semibold px-7 h-12 w-full sm:w-auto bg-primary text-primary-foreground hover:bg-primary/90" asChild>
                <Link href="/login">
                  {hero.cta} <ArrowRight className="ml-2 w-4 h-4" />
                </Link>
              </Button>
              <Button size="lg" variant="outline" className="text-sm font-semibold px-7 h-12 w-full sm:w-auto glass" asChild>
                <a href="#fleet">See a sample fleet</a>
              </Button>
            </div>
          </motion.div>

          {/* 3D topology globe + floating glass telemetry cards */}
          <div className="relative mt-12 lg:mt-16 scene-3d">
            <motion.div
              className="mx-auto w-full max-w-[620px] aspect-square"
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 1, delay: 0.15, ease: [0.22, 1, 0.36, 1] }}
              style={reduce ? undefined : { y: globeY, scale: globeScale }}
            >
              <TopologyGlobe className="w-full h-full" />
            </motion.div>

            {/* Legend */}
            <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2 -mt-4 lg:-mt-8">
              {[
                { label: "Nodes", tone: "bg-info" },
                { label: "Services", tone: "bg-violet" },
                { label: "Observability plane", tone: "bg-success" },
                { label: "Active incident", tone: "bg-primary" },
              ].map((l) => (
                <span key={l.label} className="flex items-center gap-2 eyebrow">
                  <span className={`status-dot ${l.tone}`} /> {l.label}
                </span>
              ))}
            </div>

            {/* Floating glass telemetry */}
            <motion.div
              className="hidden lg:block absolute left-0 top-16 w-56 glass glow-halo glow-edge light-sweep p-4 float-slow"
              initial={{ opacity: 0, x: -30 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.7, delay: 0.55 }}
              style={reduce ? undefined : { y: cardAY }}
            >
              <p className="eyebrow mb-1 flex items-center gap-1.5">
                <Activity className="w-3 h-3 text-primary" /> Anomaly score
              </p>
              <p className="stat-figure text-2xl text-foreground">4.2σ</p>
              <Sparkline color="hsl(var(--primary))" />
              <p className="text-[11px] text-muted-foreground">Example · compute-02 iowait vs. its weekday-hour baseline</p>
            </motion.div>

            <motion.div
              className="hidden lg:block absolute right-0 top-8 w-60 glass glow-halo glow-edge light-sweep p-4 float-slower"
              initial={{ opacity: 0, x: 30 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.7, delay: 0.7 }}
              style={reduce ? undefined : { y: cardBY }}
            >
              <p className="eyebrow mb-2 flex items-center gap-1.5">
                <Network className="w-3 h-3 text-info" /> Correlated incident · example
              </p>
              <p className="text-xs text-foreground leading-relaxed">
                18 alerts collapsed into <span className="stat-figure text-primary">1 incident</span> — probable cause
                on <span className="stat-figure">compute-02</span>.
              </p>
              <p className="eyebrow mt-3 mb-1.5">Root-cause likelihood</p>
              <div className="space-y-1.5">
                {[
                  { label: "nova-compute", pct: 94, tone: "bg-primary" },
                  { label: "ovs-br-ex", pct: 61, tone: "bg-info" },
                  { label: "neutron-l3", pct: 28, tone: "bg-violet" },
                ].map((bar) => (
                  <div key={bar.label}>
                    <div className="flex justify-between text-[11px] text-muted-foreground mb-1">
                      <span>{bar.label}</span>
                      <span className="stat-figure">{bar.pct}%</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                      <div className={`h-full rounded-full ${bar.tone}`} style={{ width: `${bar.pct}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </motion.div>

            <motion.div
              className="hidden xl:block absolute right-10 bottom-8 glass glow-halo light-sweep px-4 py-3 float-slow"
              initial={{ opacity: 0, y: 24 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, delay: 0.85 }}
            >
              <p className="eyebrow mb-0.5">Scrape interval</p>
              <p className="stat-figure text-lg text-foreground flex items-center gap-2">
                <span className="status-dot bg-success animate-pulse" /> 20 s
              </p>
            </motion.div>

            <motion.div
              className="hidden xl:block absolute left-8 bottom-10 glass glow-halo light-sweep px-4 py-3 float-slower"
              initial={{ opacity: 0, y: 24 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, delay: 0.95 }}
            >
              <p className="eyebrow mb-0.5">Autonomy</p>
              <p className="stat-figure text-lg text-foreground flex items-center gap-2">
                <span className="status-dot bg-primary" /> Level 0 · you approve
              </p>
            </motion.div>
          </div>

          {/* Live stats strip */}
          <motion.dl
            className="mt-16 lg:mt-24 grid grid-cols-2 md:grid-cols-4 gap-px bg-border/70 border border-border rounded-xl overflow-hidden"
            {...fadeUp}
            transition={{ duration: 0.55 }}
          >
            {[
              { figure: "20 s", label: "Metric scrape interval" },
              { figure: "7 × 24", label: "Weekday-hour baselines" },
              { figure: "90 days", label: "Max forecast horizon" },
              { figure: "Level 0", label: "Every fix needs approval" },
            ].map((stat) => (
              <div key={stat.label} className="relative glass light-sweep rounded-none px-6 py-5 text-center">
                <dt className="eyebrow order-2 mt-1.5">{stat.label}</dt>
                <dd className="stat-figure text-2xl lg:text-3xl text-foreground -order-1">{stat.figure}</dd>
              </div>
            ))}
          </motion.dl>
        </div>
      </section>

      {/* Fleet status */}
      <section id="fleet" className="relative z-10 py-20 lg:py-28 border-t border-border">
        <div className="max-w-7xl mx-auto px-6 lg:px-8">
          <div className="flex flex-col md:flex-row md:items-end md:justify-between mb-12 gap-4">
            <div className="max-w-xl">
              <p className="eyebrow text-primary mb-3">Fleet</p>
              <h2 className="text-3xl sm:text-4xl font-display font-bold text-foreground tracking-[-0.025em] leading-[1.08] mb-3">
                {fleet.title_line_1}
                <br />
                {fleet.title_line_2}
              </h2>
              <p className="text-muted-foreground text-base">{fleet.subhead}</p>
            </div>
            <Link href="/login" className="group inline-flex items-center gap-2 text-primary font-semibold text-sm self-start md:self-end">
              {fleet.cta_label}
              <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
            </Link>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5" aria-label="Illustrative fleet">
            {fleetNodes.map((node, i) => {
              const Icon = node.icon;
              return (
                <motion.div
                  key={node.name}
                  {...fadeUp}
                  transition={{ duration: 0.5, delay: i * 0.07, ease: [0.22, 1, 0.36, 1] }}
                >
                  <div className="h-full panel p-6 hover:shadow-[0_4px_12px_rgba(15,23,42,0.07)] transition-shadow">
                    <div className="flex items-start justify-between mb-5">
                      <div className={`w-10 h-10 rounded-lg bg-muted flex items-center justify-center ${iconClass[node.tone]}`}>
                        <Icon className="w-5 h-5" />
                      </div>
                      <span
                        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] ${toneClass[node.tone]}`}
                      >
                        <span className={`status-dot ${dotClass[node.tone]}`} />
                        {node.state}
                      </span>
                    </div>
                    <h3 className="stat-figure text-sm text-foreground">{node.name}</h3>
                    <p className="text-xs text-muted-foreground mt-1.5 leading-relaxed">{node.role}</p>
                    <p className="stat-figure text-[11px] text-primary mt-4">{node.metric}</p>
                  </div>
                </motion.div>
              );
            })}
          </div>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="relative z-10 py-20 lg:py-28 border-t border-border">
        <div className="max-w-6xl mx-auto px-6 lg:px-8">
          <motion.div className="max-w-2xl mb-14" {...fadeUp} transition={{ duration: 0.55 }}>
            <p className="eyebrow text-primary mb-3">{featuresContent.eyebrow}</p>
            <h2 className="text-3xl sm:text-4xl lg:text-5xl font-display font-bold mb-4 text-foreground tracking-[-0.025em] leading-[1.08]">
              {featuresContent.title_line_1}
              <br />
              {featuresContent.title_line_2}
            </h2>
            <p className="text-muted-foreground text-base lg:text-lg">{featuresContent.subhead}</p>
          </motion.div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {featuresContent.items.map((feature, i) => {
              const Icon = FEATURE_ICONS[i % FEATURE_ICONS.length];
              const tint = FEATURE_TINTS[i % FEATURE_TINTS.length];
              return (
                <motion.div
                  key={feature.title}
                  {...fadeUp}
                  transition={{ duration: 0.5, delay: i * 0.07, ease: [0.22, 1, 0.36, 1] }}
                >
                  <div className="h-full panel p-7 lg:p-8 hover:shadow-[0_4px_12px_rgba(15,23,42,0.07)] transition-shadow">
                    <div className={`w-10 h-10 rounded-lg flex items-center justify-center mb-5 ${tint}`}>
                      <Icon className="w-5 h-5" />
                    </div>
                    <p className="eyebrow mb-2">{feature.tag}</p>
                    <h3 className="font-display font-semibold text-xl mb-2 text-foreground tracking-[-0.015em]">
                      {feature.title}
                    </h3>
                    <p className="text-sm text-muted-foreground leading-relaxed">{feature.description}</p>
                  </div>
                </motion.div>
              );
            })}
          </div>
        </div>
      </section>

      {/* Stack strip */}
      <section className="relative z-10 py-14 border-t border-border">
        <div className="max-w-6xl mx-auto px-6 lg:px-8">
          <p className="eyebrow text-center mb-7">Built on</p>
          <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-3">
            {["OpenStack 2024.1", "Kolla-Ansible", "Prometheus", "node_exporter", "Loki", "Neo4j", "Qdrant", "LangGraph", "NVIDIA NIM", "Ansible"].map((t) => (
              <span key={t} className="stat-figure text-sm text-muted-foreground">
                {t}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* Guardrails */}
      <section id="guardrails" className="relative z-10 py-20 lg:py-28 border-t border-border">
        <div className="max-w-7xl mx-auto px-6 lg:px-8">
          <motion.div className="text-center max-w-2xl mx-auto mb-14" {...fadeUp} transition={{ duration: 0.55 }}>
            <p className="eyebrow text-primary mb-3">{guardrailsContent.eyebrow}</p>
            <h2 className="text-3xl sm:text-4xl font-display font-bold text-foreground tracking-[-0.025em] mb-3">
              {guardrailsContent.title}
            </h2>
            <p className="text-muted-foreground text-base">{guardrailsContent.subhead}</p>
          </motion.div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {guardrailsContent.items.map((item, i) => {
              const Icon = GUARDRAIL_ICONS[i % GUARDRAIL_ICONS.length];
              const tint = GUARDRAIL_TINTS[i % GUARDRAIL_TINTS.length];
              return (
                <motion.div
                  key={item.title}
                  {...fadeUp}
                  transition={{ duration: 0.5, delay: (i % 3) * 0.07, ease: [0.22, 1, 0.36, 1] }}
                >
                  <div className="h-full panel p-6 flex flex-col">
                    <div className={`w-9 h-9 rounded-lg flex items-center justify-center mb-4 ${tint}`}>
                      <Icon className="w-4 h-4" />
                    </div>
                    <p className="eyebrow mb-1.5">{item.tag}</p>
                    <h3 className="font-display font-semibold text-base text-foreground mb-2 tracking-[-0.01em]">{item.title}</h3>
                    <p className="text-foreground/80 text-sm leading-relaxed flex-1">{item.description}</p>
                  </div>
                </motion.div>
              );
            })}
          </div>
        </div>
      </section>

      {/* CTA slab */}
      <section className="relative z-10 py-20 lg:py-28 border-t border-border">
        <div className="max-w-6xl mx-auto px-6 lg:px-8">
          <motion.div
            className={`${styles.landingCta} rounded-2xl px-6 py-16 lg:py-20 text-center relative overflow-hidden`}
            {...fadeUp}
            transition={{ duration: 0.6 }}
          >
            <div
              className="absolute inset-0 pointer-events-none"
              aria-hidden="true"
              style={{ background: "radial-gradient(ellipse at 50% 120%, hsl(var(--primary) / 0.25), transparent 60%)" }}
            />
            <div className="relative">
              <p className="eyebrow mb-4" style={{ color: "hsl(var(--primary))" }}>Get started</p>
              <h2 className="text-3xl sm:text-4xl lg:text-5xl font-display font-bold tracking-[-0.025em] leading-[1.08] mb-4">
                {ctaContent.title_line_1}
                <br />
                {ctaContent.title_line_2}
              </h2>
              <p className="text-sm lg:text-base opacity-70 max-w-md mx-auto mb-9">{ctaContent.subhead}</p>
              <Button size="lg" className="text-sm font-semibold px-8 h-12 bg-primary text-primary-foreground hover:bg-primary/90" asChild>
                <Link href="/login">
                  {ctaContent.cta_label} <ArrowRight className="ml-2 w-4 h-4" />
                </Link>
              </Button>
              <p className="stat-figure text-[11px] opacity-50 mt-6">Self-hosted · Built for OpenStack 2024.1</p>
            </div>
          </motion.div>
        </div>
      </section>

      {/* Footer */}
      <footer className="relative z-10 border-t border-border">
        <div className="max-w-7xl mx-auto px-6 lg:px-8 py-12">
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-8">
            <div>
              <Logo size="md" />
              <p className="text-sm text-muted-foreground mt-3 max-w-xs">
                An AI cockpit for OpenStack private clouds: monitor, diagnose, forecast and fix — with your approval.
              </p>
            </div>
            <nav className="flex flex-wrap gap-x-8 gap-y-3 text-sm font-medium text-muted-foreground">
              <a href="#fleet" className="hover:text-foreground transition-colors">Fleet</a>
              <a href="#features" className="hover:text-foreground transition-colors">Platform</a>
              <a href="#guardrails" className="hover:text-foreground transition-colors">Guardrails</a>
              <Link href="/login" className="hover:text-foreground transition-colors">Log in</Link>
            </nav>
          </div>
          <div className="mt-10 pt-6 border-t border-border flex flex-col sm:flex-row items-center justify-between gap-3">
            <p className="stat-figure text-[11px] text-muted-foreground">© 2026 Cortex</p>
            <p className="stat-figure text-[11px] text-muted-foreground flex items-center gap-2">
              Built for OpenStack 2024.1 (Caracal)
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default Landing;
