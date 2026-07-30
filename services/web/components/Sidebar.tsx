"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutGrid,
  Server,
  Boxes,
  Waypoints,
  Network,
  Gauge,
  ScrollText,
  Siren,
  ShieldAlert,
  Bot,
  Workflow,
  ShieldCheck,
  Settings2,
  LifeBuoy,
} from "lucide-react";

const NAV_SECTIONS = [
  { title: "Dashboard", href: "/dashboard", icon: LayoutGrid },
  {
    title: "Infrastructure",
    items: [
      { title: "Nodes", href: "/nodes", icon: Server },
      { title: "Services", href: "/services", icon: Boxes },
      { title: "Networks", href: "/networks", icon: Network },
      { title: "Topology", href: "/topology", icon: Waypoints },
    ],
  },
  {
    title: "Monitoring",
    items: [
      { title: "Metrics", href: "/metrics", icon: Gauge },
      { title: "Logs", href: "/logs", icon: ScrollText },
      { title: "Alerts", href: "/alerts", icon: Siren },
      { title: "Incidents", href: "/incidents", icon: ShieldAlert },
    ],
  },
  { title: "AI Copilot", href: "/copilot", icon: Bot },
  { title: "Operations", href: "/operations", icon: Workflow },
  { title: "Administration", href: "/admin", icon: ShieldCheck },
];

const FOOTER_ITEMS = [
  { title: "Settings", href: "/settings", icon: Settings2 },
  { title: "Support", href: "/support", icon: LifeBuoy },
];

function NavLink({ href, icon: Icon, title, active }: { href: string; icon: any; title: string; active: boolean }) {
  return (
    <Link
      href={href}
      className="relative flex items-center gap-2.5 rounded-[var(--radius-control)] py-1.5 pl-2.5 pr-2.5 text-[13px] transition-colors"
      style={{
        color: active ? "var(--text)" : "var(--text-dim)",
        background: active ? "var(--accent-soft)" : "transparent",
        fontWeight: active ? 500 : 400,
      }}
    >
      {active && (
        <span
          className="absolute left-0 top-1/2 h-4 w-[2px] -translate-y-1/2 rounded-full"
          style={{ background: "var(--accent)" }}
        />
      )}
      <Icon className="h-[16px] w-[16px] shrink-0" strokeWidth={1.75} style={{ color: active ? "var(--accent)" : "var(--text-muted)" }} />
      <span>{title}</span>
    </Link>
  );
}

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sticky top-4 h-[calc(100vh-2rem)] w-[224px] shrink-0 panel flex flex-col p-3">
      <div className="mb-4 flex items-center gap-2 px-1">
        <div
          className="flex h-7 w-7 items-center justify-center rounded-[7px] text-sm font-semibold text-white"
          style={{ background: "var(--accent)" }}
        >
          C
        </div>
        <div className="font-display text-[15px] font-semibold text-color-text">Cortex</div>
      </div>

      <nav className="flex-1 space-y-3 " aria-label="Primary navigation">
        {NAV_SECTIONS.map((section, index) => (
          <div key={index}>
            {section.items ? (
              <div className="space-y-0.5">
                <div className="eyebrow px-2.5 pb-1">{section.title}</div>
                {section.items.map((item) => (
                  <NavLink key={item.href} {...item} active={!!pathname?.startsWith(item.href)} />
                ))}
              </div>
            ) : (
              <NavLink {...section} active={!!pathname?.startsWith(section.href)} />
            )}
          </div>
        ))}
      </nav>

      <div className="mt-3 space-y-0.5 border-t pt-3" style={{ borderColor: "var(--border-soft)" }}>
        {FOOTER_ITEMS.map((item) => (
          <NavLink key={item.href} {...item} active={!!pathname?.startsWith(item.href)} />
        ))}

        <div className="mt-2.5 flex items-center gap-2 rounded-[var(--radius-control)] p-1.5" style={{ background: "var(--canvas)" }}>
          <div
            className="grid h-8 w-8 flex-shrink-0 place-items-center rounded-full text-xs font-semibold"
            style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
          >
            AM
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-color-text">Alex Morgan</div>
            <div className="truncate text-xs text-text-faint">Product Ops</div>
          </div>
        </div>
      </div>
    </aside>
  );
}
