"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import { Grid, Activity, Users, Cpu, Server, Settings2, HelpCircle, UserCircle } from "lucide-react";

const NAV_SECTIONS = [
  { title: "Dashboard", href: "/dashboard", icon: Grid },
  {
    title: "Infrastructure",
    items: [
      { title: "Nodes", href: "/nodes", icon: Server },
      { title: "Services", href: "/services", icon: Server },
      { title: "Networks", href: "/networks", icon: Activity },
      { title: "Topology", href: "/topology", icon: Activity },
    ],
  },
  {
    title: "Monitoring",
    items: [
      { title: "Metrics", href: "/metrics", icon: Cpu },
      { title: "Logs", href: "/logs", icon: Activity },
      { title: "Alerts", href: "/alerts", icon: Activity },
      { title: "Incidents", href: "/incidents", icon: Activity },
    ],
  },
  { title: "AI Copilot", href: "/copilot", icon: Activity },
  { title: "Operations", href: "/operations", icon: Activity },
  { title: "Administration", href: "/admin", icon: Users },
];

const FOOTER_ITEMS = [
  { title: "Settings", href: "/settings", icon: Settings2 },
  { title: "Support", href: "/support", icon: HelpCircle },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <motion.aside
      initial={{ opacity: 0, x: -24 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      className="sticky top-8 h-[calc(100vh-4rem)] w-[250px] shrink-0 rounded-[20px] bg-white border border-[#ECECEC] p-6 shadow-[0_2px_8px_rgba(0,0,0,0.04)]"
    >
      <div className="mb-8">
        <div className="text-lg font-semibold text-color-text">Cortex</div>
        <p className="mt-2 text-sm leading-6 text-text-faint">AI operations for modern infrastructure.</p>
      </div>

      <nav className="space-y-3" aria-label="Primary navigation">
        {NAV_SECTIONS.map((section, index) => (
          <div key={index}>
            {section.items ? (
              <div className="space-y-2">
                <div className="text-xs font-semibold uppercase tracking-[0.24em] text-text-faint">{section.title}</div>
                <div className="space-y-2">
                  {section.items.map((item) => {
                    const active = pathname?.startsWith(item.href);
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        className={`flex items-center gap-3 rounded-2xl px-3 py-3 text-sm transition ${
                          active ? "bg-orange-50 text-orange-600" : "text-text-dim hover:bg-slate-100"
                        }`}
                      >
                        <item.icon className={`h-4 w-4 ${active ? "text-orange-600" : "text-text-muted"}`} />
                        <span>{item.title}</span>
                      </Link>
                    );
                  })}
                </div>
              </div>
            ) : (
              <Link
                href={section.href}
                className={`flex items-center gap-3 rounded-2xl px-3 py-3 text-sm transition ${
                  pathname?.startsWith(section.href) ? "bg-orange-50 text-orange-600" : "text-text-dim hover:bg-slate-100"
                }`}
              >
                <section.icon className="h-4 w-4" />
                <span>{section.title}</span>
              </Link>
            )}
          </div>
        ))}
      </nav>

      <div className="mt-8 border-t border-[#ECECEC] pt-6">
        <div className="space-y-3">
          {FOOTER_ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="flex items-center gap-3 rounded-2xl px-3 py-3 text-sm text-text-dim transition hover:bg-slate-100"
            >
              <item.icon className="h-4 w-4" />
              <span>{item.title}</span>
            </Link>
          ))}
        </div>

        <div className="mt-6 border-t border-[#ECECEC] pt-6">
          <div className="flex items-center gap-3 rounded-[18px] border border-[#ECECEC] bg-[#F8FAFC] p-4">
            <div className="grid h-11 w-11 place-items-center rounded-2xl bg-orange-50 text-orange-600">
              <UserCircle className="h-5 w-5" />
            </div>
            <div>
              <div className="text-sm font-semibold text-color-text">Alex Morgan</div>
              <div className="text-xs text-text-faint">Product Ops</div>
            </div>
          </div>
        </div>
      </div>
    </motion.aside>
  );
}
