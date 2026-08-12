"use client";

import { LayoutGrid, Rows3, Search, X } from "lucide-react";

export type EntityView = "cards" | "table";

export interface EntityFilter {
  key: string;
  label: string; // "All sources", "All states" -- the select's first option
  value: string; // "all" or the active filter value
  options: { value: string; label: string; count?: number }[];
  onChange: (value: string) => void;
}

/** Search input + N select-filters + a card/table view toggle, in the same
 * visual language as the search/filter bar on Logs (LogViewer.tsx) and the
 * label filter chips + zoom controls on Topology (TopologyGraph.tsx) --
 * reused here instead of inventing a third toolbar style. */
export default function EntityToolbar({
  query,
  onQueryChange,
  placeholder = "Search…",
  filters = [],
  view,
  onViewChange,
  resultCount,
  resultNoun = "result",
}: {
  query: string;
  onQueryChange: (value: string) => void;
  placeholder?: string;
  filters?: EntityFilter[];
  view: EntityView;
  onViewChange: (view: EntityView) => void;
  resultCount?: number;
  resultNoun?: string;
}) {
  const selectClass = "rounded-[var(--radius-control)] px-3 py-2 text-sm text-color-text outline-none transition-colors";
  const selectStyle = { border: "1px solid var(--border)", background: "var(--canvas)" } as const;

  return (
    <div className="panel grid gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" strokeWidth={2} />
          <input
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder={placeholder}
            className="w-full rounded-[var(--radius-control)] py-2.5 pl-9 pr-8 text-sm text-color-text outline-none transition-colors"
            style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
          />
          {query && (
            <button
              onClick={() => onQueryChange("")}
              aria-label="Clear search"
              className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-text-faint hover:text-color-text"
            >
              <X className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
          )}
        </div>

        <div
          className="inline-flex flex-shrink-0 rounded-[var(--radius-control)] p-0.5"
          style={{ border: "1px solid var(--border)" }}
          role="group"
          aria-label="Switch view"
        >
          <button
            onClick={() => onViewChange("cards")}
            aria-pressed={view === "cards"}
            title="Card view"
            className="inline-flex items-center gap-1.5 rounded-[5px] px-2.5 py-1.5 text-xs font-medium transition-colors"
            style={{
              background: view === "cards" ? "var(--accent)" : "transparent",
              color: view === "cards" ? "#fff" : "var(--text-dim)",
            }}
          >
            <LayoutGrid className="h-3.5 w-3.5" strokeWidth={2} />
            Cards
          </button>
          <button
            onClick={() => onViewChange("table")}
            aria-pressed={view === "table"}
            title="Table view"
            className="inline-flex items-center gap-1.5 rounded-[5px] px-2.5 py-1.5 text-xs font-medium transition-colors"
            style={{
              background: view === "table" ? "var(--accent)" : "transparent",
              color: view === "table" ? "#fff" : "var(--text-dim)",
            }}
          >
            <Rows3 className="h-3.5 w-3.5" strokeWidth={2} />
            Table
          </button>
        </div>
      </div>

      {(filters.length > 0 || resultCount !== undefined) && (
        <div className="flex flex-wrap items-center gap-2.5">
          {filters.map((f) => (
            <select
              key={f.key}
              value={f.value}
              onChange={(e) => f.onChange(e.target.value)}
              className={selectClass}
              style={selectStyle}
            >
              <option value="all">{f.label}</option>
              {f.options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                  {o.count !== undefined ? ` (${o.count})` : ""}
                </option>
              ))}
            </select>
          ))}

          {resultCount !== undefined && (
            <span className="ml-auto text-xs text-text-faint">
              <span className="stat-figure text-color-text">{resultCount}</span> {resultNoun}
              {resultCount === 1 ? "" : "s"}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
