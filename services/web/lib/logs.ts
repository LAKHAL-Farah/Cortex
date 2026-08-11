import type { LogLevel } from "./types";

export const ALL_LEVELS: LogLevel[] = ["ERROR", "WARNING", "INFO", "DEBUG"];

export const LEVEL_COLOR: Record<LogLevel, string> = {
  ERROR: "var(--crit)",
  WARNING: "var(--warn)",
  INFO: "var(--ok)",
  DEBUG: "var(--neutral)",
};

export const LEVEL_SOFT: Record<LogLevel, string> = {
  ERROR: "var(--crit-soft)",
  WARNING: "var(--warn-soft)",
  INFO: "var(--ok-soft)",
  DEBUG: "var(--neutral-soft)",
};

/** Best-effort level extraction from a raw log line. Structured service logs
 * carry an explicit level token (see the sandbox log simulator); plain
 * syslog lines won't match and callers should treat that as "unknown". */
export function parseLevel(line: string): LogLevel | null {
  const m = line.match(/\b(DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL|NOTICE)\b/);
  if (!m) return null;
  const token = m[1];
  if (token === "WARN") return "WARNING";
  if (token === "CRITICAL") return "ERROR";
  if (token === "NOTICE") return "INFO";
  return token as LogLevel;
}

/** Compact "3m ago" style relative time for dashboard widgets. */
export function formatRelativeTime(ts: number): string {
  const diffSec = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHour = Math.round(diffMin / 60);
  if (diffHour < 24) return `${diffHour}h ago`;
  const diffDay = Math.round(diffHour / 24);
  return `${diffDay}d ago`;
}
