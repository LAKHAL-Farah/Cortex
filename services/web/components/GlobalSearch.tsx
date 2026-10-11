"use client";

import React from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import {
  Search, LayoutGrid, Server, Boxes, Network, Waypoints, Gauge, ScrollText, Siren, TrendingUp, CalendarClock,
  SlidersHorizontal, Wallet, Bot, ShieldAlert, ClipboardCheck, ShieldCheck, Settings2, CornerDownLeft,
} from "lucide-react";
import type { Node as CortexNode } from "@/lib/types";
import { useCurrentUser } from "@/lib/useCurrentUser";

type Result = { id: string; label: string; hint: string; group: "Pages" | "Nodes"; href: string; icon: typeof Search };

const PAGES: { label: string; href: string; icon: typeof Search; keywords?: string; roles?: string[] }[] = [
  { label: "Dashboard", href: "/dashboard", icon: LayoutGrid, keywords: "home overview" },
  { label: "Nodes", href: "/nodes", icon: Server, keywords: "hosts servers machines" },
  { label: "Services", href: "/services", icon: Boxes, keywords: "openstack nova neutron" },
  { label: "Networks", href: "/networks", icon: Network, keywords: "subnet router floating ip" },
  { label: "Topology", href: "/topology", icon: Waypoints, keywords: "graph map" },
  { label: "Metrics", href: "/metrics", icon: Gauge, keywords: "cpu memory disk" },
  { label: "Logs", href: "/logs", icon: ScrollText, keywords: "loki syslog" },
  { label: "Alerts", href: "/alerts", icon: Siren, keywords: "anomalies incidents rca" },
  { label: "Alert history", href: "/alerts/history", icon: Siren, keywords: "past events" },
  { label: "Baselines", href: "/baselines", icon: TrendingUp },
  { label: "Forecast", href: "/forecast", icon: CalendarClock, keywords: "prediction threshold" },
  { label: "Capacity What-If", href: "/capacity", icon: SlidersHorizontal, keywords: "simulator" },
  { label: "Quotas & Budget", href: "/quotas", icon: Wallet, keywords: "finops cost showback chargeback" },
  { label: "AI Copilot", href: "/copilot", icon: Bot, keywords: "chat assistant" },
  { label: "Security", href: "/security", icon: ShieldAlert, keywords: "vulnerabilities cve ports" },
  { label: "Remediation audit", href: "/remediation-audit", icon: ClipboardCheck, keywords: "approvals", roles: ["admin", "operator"] },
  { label: "Administration", href: "/admin", icon: ShieldCheck, keywords: "users roles", roles: ["admin"] },
  { label: "Settings", href: "/settings", icon: Settings2, keywords: "email digest sensitivity" },
];

const nodesFetcher = async (url: string): Promise<CortexNode[]> => {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) return [];
  const data = await res.json().catch(() => []);
  return Array.isArray(data) ? data : [];
};

export default function GlobalSearch() {
  const router = useRouter();
  const { user } = useCurrentUser();
  const role = user?.role;
  const inputRef = React.useRef<HTMLInputElement>(null);
  const boxRef = React.useRef<HTMLDivElement>(null);
  const [q, setQ] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const [active, setActive] = React.useState(0);

  // Only fetch nodes once the user starts searching.
  const { data: nodes } = useSWR<CortexNode[]>(open ? "/api/nodes" : null, nodesFetcher, { revalidateOnFocus: false });

  const results = React.useMemo<Result[]>(() => {
    const term = q.trim().toLowerCase();
    const pages: Result[] = PAGES.filter((p) => !p.roles || (role && p.roles.includes(role)))
      .filter((p) => !term || `${p.label} ${p.keywords ?? ""}`.toLowerCase().includes(term))
      .map((p) => ({ id: `page:${p.href}`, label: p.label, hint: p.href, group: "Pages", href: p.href, icon: p.icon }));
    const nodeResults: Result[] = term
      ? (nodes ?? [])
          .filter((n) => `${n.hostname} ${n.ip_address} ${n.role}`.toLowerCase().includes(term))
          .slice(0, 6)
          .map((n) => ({
            id: `node:${n.id}`,
            label: n.hostname,
            hint: `${n.role} · ${n.ip_address}`,
            group: "Nodes",
            href: `/nodes/${encodeURIComponent(n.id)}`,
            icon: Server,
          }))
      : [];
    return [...nodeResults, ...pages].slice(0, 10);
  }, [q, nodes, role]);

  // "/" or Ctrl/⌘+K focuses the search from anywhere.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  React.useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const go = (r?: Result) => {
    if (!r) return;
    setOpen(false);
    setQ("");
    (document.activeElement as HTMLElement | null)?.blur();
    router.push(r.href);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(results[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
      inputRef.current?.blur();
    }
  };

  return (
    <div ref={boxRef} className="relative">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
      <input
        ref={inputRef}
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onKeyDown={onKeyDown}
        placeholder="Search the cockpit..."
        className="w-full rounded-[var(--radius-control)] py-2.5 pl-9 pr-16 text-sm text-color-text outline-none transition-colors"
        style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
        onFocus={(e) => {
          e.currentTarget.style.borderColor = "var(--accent)";
          setOpen(true);
        }}
        onBlur={(e) => (e.currentTarget.style.borderColor = "var(--border)")}
        aria-label="Global search"
        role="combobox"
        aria-expanded={open}
        aria-controls="global-search-results"
      />
      <kbd
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rounded px-1.5 py-0.5 text-[10px] text-text-muted"
        style={{ border: "1px solid var(--border)", fontFamily: "var(--font-mono)" }}
      >
        Ctrl K
      </kbd>

      {open && (
        <div id="global-search-results" role="listbox" className="panel absolute left-0 right-0 z-40 mt-2 max-h-[380px] overflow-y-auto py-1.5" style={{ boxShadow: "var(--shadow-hover)" }}>
          {results.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-text-faint">No matches for “{q}”</div>
          ) : (
            results.map((r, i) => {
              const header = i === 0 || results[i - 1].group !== r.group ? r.group : null;
              const Icon = r.icon;
              const isActive = i === active;
              return (
                <React.Fragment key={r.id}>
                  {header && <div className="eyebrow px-4 pb-1 pt-2">{header}</div>}
                  <button
                    role="option"
                    aria-selected={isActive}
                    onMouseEnter={() => setActive(i)}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => go(r)}
                    className="flex w-full items-center gap-3 px-4 py-2 text-left"
                    style={{ background: isActive ? "var(--accent-soft)" : "transparent" }}
                  >
                    <Icon className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} style={{ color: isActive ? "var(--accent)" : "var(--text-muted)" }} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] text-color-text">{r.label}</span>
                      <span className="block truncate text-[11px] text-text-faint">{r.hint}</span>
                    </span>
                    {isActive && <CornerDownLeft className="h-3.5 w-3.5 text-text-muted" strokeWidth={1.75} />}
                  </button>
                </React.Fragment>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
