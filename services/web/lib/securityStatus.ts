import type { AgentCveMatch, AgentEbpfAlert, AgentExposedPortMismatch, AgentSecGroupInfo, AgentSecGroupRule, AgentSecGroupSignal, AgentSecurityData, AgentSecuritySignal, SecurityFinding } from "./types";

/** Same {has_signal, degraded, restricted} -> tone/label mapping
 * components/CopilotAgentPanels.tsx's SecuritySignalRow uses for the chat
 * trace view. Shared here (rather than redefined per page) so every
 * Security dashboard page and the chat answer keep saying the same thing
 * about the same host -- see §6's own "the dashboard and the chat answer
 * say the same thing about the same host" requirement.
 */
export function securityPillTone(signal: AgentSecuritySignal) {
  if (signal.restricted) return { color: "var(--text-muted)", soft: "var(--canvas)", label: "Admin only" };
  if (signal.degraded) return { color: "var(--warn)", soft: "var(--warn-soft)", label: "Unknown" };
  if (signal.has_signal) return { color: "var(--crit)", soft: "var(--crit-soft)", label: "Flagged" };
  return { color: "var(--ok)", soft: "var(--ok-soft)", label: "Clean" };
}

/** Phase Sec-3: which of a host's *currently attached* security groups
 * (sig.data.security_groups) are the actual one(s) carrying a signal --
 * either a static overly-permissive rule (risky_rules, matched by group
 * name -- that's the only identifier security_audit.diff_security_
 * groups' risky-rule entries carry) or a change since the last snapshot
 * (drift, matched by group id). Everything upstream of this only ever
 * reported aggregate counts ("3 risky rules somewhere on this host"); this
 * is what lets a page point at the specific group instead.
 */
export function flaggedSecurityGroupKeys(sig: AgentSecGroupSignal): { names: Set<string>; ids: Set<string> } {
  const names = new Set((sig.risky_rules ?? []).map((r) => r.security_group));
  const ids = new Set((sig.drift ?? []).map((d) => d.security_group_id));
  return { names, ids };
}

export function isSecurityGroupFlagged(
  group: AgentSecGroupInfo,
  flagged: { names: Set<string>; ids: Set<string> },
): boolean {
  return flagged.names.has(group.name) || flagged.ids.has(group.id);
}

/** Phase Sec-4: one tone for a whole host's card on the /security and
 * /security/security-groups grids, aggregated across all four sub-checks
 * the same worst-first ordering securityPillTone applies to a single
 * signal -- any signal fired anywhere on this host outranks any signal
 * merely being degraded, which outranks a fully clean host.
 */
export function overallSecurityTone(raw: AgentSecurityData) {
  const signals = [raw.auth_signal, raw.sec_group_signal, raw.cve_signal, raw.exposed_port_signal, raw.ebpf_signal];
  if (signals.some((s) => s.has_signal)) return { color: "var(--crit)", soft: "var(--crit-soft)", label: "Flagged" };
  if (signals.some((s) => s.degraded)) return { color: "var(--warn)", soft: "var(--warn-soft)", label: "Degraded" };
  return { color: "var(--ok)", soft: "var(--ok-soft)", label: "Clean" };
}

/** Phase Sec-4: a stable identity for one security-group rule, used to
 * cross-reference the *same* rule across three differently-shaped places
 * it can show up -- `data.security_groups[].rules` (current state),
 * `risky_rules[].rule` (static baseline hit), and
 * `drift[].added_rules`/`removed_rules` (change vs. last snapshot).
 * None of those three carry a shared rule id (Neutron rules don't have
 * one exposed here), so identity is the tuple that actually defines a
 * rule's meaning: direction, protocol, port range, and remote CIDR.
 */
function ruleKey(rule: AgentSecGroupRule["rule"]): string {
  return [rule.direction, rule.protocol ?? "*", rule.port_range_min ?? "*", rule.port_range_max ?? "*", rule.remote_ip_prefix ?? "*"].join("|");
}

export interface AnnotatedRule {
  rule: AgentSecGroupRule["rule"];
  riskyReason: string | null;
  driftStatus: "added" | null;
}

export interface GroupInsight {
  group: AgentSecGroupInfo;
  isFlagged: boolean;
  rules: AnnotatedRule[];
  // Rules the last stored snapshot had for this group that aren't in its
  // current rule list any more -- only knowable from `drift`, since a
  // removed rule by definition isn't in `data.security_groups` any more.
  removedRules: AgentSecGroupRule["rule"][];
  driftSince: string | null;
}

/** Phase Sec-4: the single "what does this group actually contain, right
 * now, and why should I care" view -- merges what used to be three
 * separate, harder-to-cross-reference sections (the plain current-groups
 * listing, the overly-permissive rules list, and the drift list) into one
 * card per group: every current rule, with the specific reason inline
 * wherever it's also a risky_rules hit, plus anything added or removed
 * since the last snapshot for that same group.
 */
export function buildGroupInsights(sig: AgentSecGroupSignal): GroupInsight[] {
  const groups = sig.data?.security_groups ?? [];
  const flagged = flaggedSecurityGroupKeys(sig);

  const riskyByGroup = new Map<string, Map<string, string>>(); // group name -> ruleKey -> reason
  for (const r of sig.risky_rules ?? []) {
    if (!riskyByGroup.has(r.security_group)) riskyByGroup.set(r.security_group, new Map());
    riskyByGroup.get(r.security_group)!.set(ruleKey(r.rule), r.reason);
  }

  const driftByGroupId = new Map<string, { addedKeys: Set<string>; removed: AgentSecGroupRule["rule"][]; since: string }>();
  for (const d of sig.drift ?? []) {
    driftByGroupId.set(d.security_group_id, {
      addedKeys: new Set(d.added_rules.map(ruleKey)),
      removed: d.removed_rules,
      since: d.previous_captured_at,
    });
  }

  const insights: GroupInsight[] = groups.map((group) => {
    const riskyMap = riskyByGroup.get(group.name);
    const drift = driftByGroupId.get(group.id);
    return {
      group,
      isFlagged: isSecurityGroupFlagged(group, flagged),
      rules: group.rules.map((rule) => {
        const key = ruleKey(rule);
        return {
          rule,
          riskyReason: riskyMap?.get(key) ?? null,
          driftStatus: drift?.addedKeys.has(key) ? "added" : null,
        };
      }),
      removedRules: drift?.removed ?? [],
      driftSince: drift?.since ?? null,
    } as GroupInsight;
  });

  // A drift entry can reference a group id that isn't in `groups` any
  // more (the whole security group was deleted between the last snapshot
  // and now, not just one of its rules) -- rare, but silently dropping it
  // would mean a real change never shows up anywhere on this page.
  const knownGroupIds = new Set(groups.map((g) => g.id));
  for (const d of sig.drift ?? []) {
    if (knownGroupIds.has(d.security_group_id)) continue;
    insights.push({
      group: { id: d.security_group_id, name: `${d.security_group} (deleted since last snapshot)`, rules: [] },
      isFlagged: true,
      rules: [],
      removedRules: d.removed_rules,
      driftSince: d.previous_captured_at,
    });
    knownGroupIds.add(d.security_group_id);
  }

  return insights;
}

// --- Phase Sec-3 (vulnerabilities page): CVE severity vocabulary --------
//
// cve_feed._CVE_DATABASE only ever hand-picks entries at these four
// severities (services/api/app/services/cve_feed.py), but AgentCveMatch's
// own type keeps `severity: ... | string` since this table is explicitly
// described there as "small, hand-picked, illustrative" and expected to
// grow -- so every helper below falls back to a neutral, still-labeled
// tone instead of silently mis-coloring or crashing on a severity string
// it doesn't recognize yet.
export const CVE_SEVERITY_ORDER = ["critical", "high", "medium", "low"] as const;
export type KnownCveSeverity = (typeof CVE_SEVERITY_ORDER)[number];

const CVE_SEVERITY_COLOR: Record<KnownCveSeverity, string> = {
  critical: "var(--crit)",
  high: "var(--warn)",
  medium: "var(--medium)",
  low: "var(--text-muted)",
};

const CVE_SEVERITY_SOFT: Record<KnownCveSeverity, string> = {
  critical: "var(--crit-soft)",
  high: "var(--warn-soft)",
  medium: "var(--medium-soft)",
  low: "var(--canvas)",
};

function isKnownCveSeverity(severity: string): severity is KnownCveSeverity {
  return (CVE_SEVERITY_ORDER as readonly string[]).includes(severity);
}

export function cveSeverityTone(severity: string): { color: string; soft: string; label: string } {
  if (isKnownCveSeverity(severity)) {
    return { color: CVE_SEVERITY_COLOR[severity], soft: CVE_SEVERITY_SOFT[severity], label: severity[0].toUpperCase() + severity.slice(1) };
  }
  // An unrecognized severity string (a future _CVE_DATABASE entry outside
  // today's four) still renders, just without pretending to know how
  // dangerous it is relative to the known four.
  return { color: "var(--text-dim)", soft: "var(--canvas)", label: severity || "Unknown" };
}

/** Sorts by severity first (worst first, unknown severities last), then
 * by CVE id for a stable order within the same severity -- used by the
 * matches table so "worst finding first" doesn't depend on whatever
 * order match_cves happened to walk the package list in. */
export function cveSeverityRank(severity: string): number {
  const idx = CVE_SEVERITY_ORDER.indexOf(severity as KnownCveSeverity);
  return idx === -1 ? CVE_SEVERITY_ORDER.length : idx;
}

/** One row per (host, matched CVE) -- the flat shape the /security/
 * vulnerabilities table actually renders, straight off match_cves'
 * existing AgentCveMatch shape (cve_feed.py) plus the host it was found
 * on, since a single AgentCveMatch on its own doesn't say which node it
 * came from. */
export interface CveFindingRow extends AgentCveMatch {
  hostname: string;
  role: string;
}

/** Flattens every finding's cve_signal.matches into one row list across
 * the whole fleet, sorted worst-severity-first. A restricted (viewer)
 * signal or a degraded (collector-unreachable) one contributes no rows
 * here by construction -- both have `matches` undefined, never a
 * fabricated empty-but-clean list -- see buildCveHostStatuses below for
 * how those hosts are still represented instead of silently vanishing.
 */
export function flattenCveFindings(findings: SecurityFinding[]): CveFindingRow[] {
  const rows: CveFindingRow[] = [];
  for (const f of findings) {
    for (const match of f.raw_data.cve_signal.matches ?? []) {
      rows.push({ ...match, hostname: f.hostname, role: f.role });
    }
  }
  return rows.sort((a, b) => cveSeverityRank(a.severity) - cveSeverityRank(b.severity) || a.cve_id.localeCompare(b.cve_id));
}

/** Phase Sec-3: every monitored host's CVE-match status, independent of
 * whether it actually has a match right now -- a flat matches table on
 * its own would only ever show hosts *with* a finding, making a clean or
 * degraded host indistinguishable from one that was never checked at
 * all. This is what lets the page say "these N hosts were checked and
 * are clean" instead of just going quiet about them.
 */
export interface CveHostStatus {
  hostname: string;
  role: string;
  tone: ReturnType<typeof securityPillTone>;
  matchCount: number;
}

export function buildCveHostStatuses(findings: SecurityFinding[]): CveHostStatus[] {
  return findings.map((f) => ({
    hostname: f.hostname,
    role: f.role,
    tone: securityPillTone(f.raw_data.cve_signal),
    matchCount: f.raw_data.cve_signal.matches?.length ?? 0,
  }));
}

// --- Phase Sec-4 (kernel signals / eBPF page): priority vocabulary -------
//
// Mirrors the CVE severity section immediately above: a small, known
// vocabulary with color/label/rank, falling back to a neutral, still-
// labeled tone for anything outside it instead of crashing or silently
// mis-coloring. `ebpf_signal.py`'s own `_PRIORITY_RANK` recognizes more
// synonyms server-side (emergency/alert as aliases for critical,
// informational as an alias for info) purely to rank a real Falco/
// Tetragon feed's exact wording; the four buckets below are what this
// dashboard actually renders, since every alert this sandbox (or a real
// Falco deployment's common rule set) produces already normalizes to one
// of these.
export const EBPF_PRIORITY_ORDER = ["critical", "warning", "notice", "info"] as const;
export type KnownEbpfPriority = (typeof EBPF_PRIORITY_ORDER)[number];

const EBPF_PRIORITY_COLOR: Record<KnownEbpfPriority, string> = {
  critical: "var(--crit)",
  warning: "var(--warn)",
  notice: "var(--medium)",
  info: "var(--text-muted)",
};

const EBPF_PRIORITY_SOFT: Record<KnownEbpfPriority, string> = {
  critical: "var(--crit-soft)",
  warning: "var(--warn-soft)",
  notice: "var(--medium-soft)",
  info: "var(--canvas)",
};

function isKnownEbpfPriority(priority: string): priority is KnownEbpfPriority {
  return (EBPF_PRIORITY_ORDER as readonly string[]).includes(priority.toLowerCase());
}

export function ebpfPriorityTone(priority: string): { color: string; soft: string; label: string } {
  const normalized = priority.toLowerCase();
  if (isKnownEbpfPriority(normalized)) {
    return { color: EBPF_PRIORITY_COLOR[normalized], soft: EBPF_PRIORITY_SOFT[normalized], label: normalized[0].toUpperCase() + normalized.slice(1) };
  }
  return { color: "var(--text-dim)", soft: "var(--canvas)", label: priority || "Unknown" };
}

/** Sorts by priority first (most severe first, unrecognized priorities
 * last), then most-recent-first within the same priority -- matches
 * `ebpf_signal.get_node_ebpf_alerts`'s own "most severe first" contract
 * (it already re-sorts by `_PRIORITY_RANK` before returning), so the
 * fleet-wide table reads the same order a single-host chat answer would.
 */
export function ebpfPriorityRank(priority: string): number {
  const idx = EBPF_PRIORITY_ORDER.indexOf(priority.toLowerCase() as KnownEbpfPriority);
  return idx === -1 ? EBPF_PRIORITY_ORDER.length : idx;
}

/** One row per (host, currently-active eBPF alert) -- the flat shape
 * /security/kernel-signals' table renders, straight off
 * AgentEbpfSignal.alerts (nodes/security.py's `_check_ebpf_signal`,
 * services/ebpf_signal.py) plus the host each alert came from. A
 * restricted (viewer) or degraded (sensor bridge unreachable) signal
 * contributes no rows here by construction -- both have `alerts`
 * undefined, never a fabricated empty-but-clean list -- see
 * buildEbpfHostStatuses below for how those hosts are still represented
 * instead of silently vanishing.
 */
export interface EbpfAlertRow extends AgentEbpfAlert {
  hostname: string;
  role: string;
}

export function flattenEbpfFindings(findings: SecurityFinding[]): EbpfAlertRow[] {
  const rows: EbpfAlertRow[] = [];
  for (const f of findings) {
    for (const alert of f.raw_data.ebpf_signal.alerts ?? []) {
      rows.push({ ...alert, hostname: f.hostname, role: f.role });
    }
  }
  return rows.sort((a, b) => ebpfPriorityRank(a.priority) - ebpfPriorityRank(b.priority) || new Date(b.time).getTime() - new Date(a.time).getTime());
}

/** Every monitored host's eBPF-signal status, independent of whether it
 * actually has an active alert right now -- same reasoning
 * buildCveHostStatuses' own docstring gives: a flat alerts table on its
 * own would only ever show hosts *with* a finding, making a clean host
 * (checked, sensor deployed, nothing fired), a degraded one (no sensor
 * reachable for this host -- "unknown", not "clean"), and a restricted
 * one (viewer role) indistinguishable from a host nobody's watching at
 * all. Phase Sec-4 is a compute-only pilot, so controller/storage hosts
 * are expected to show "Clean" here (a reachable bridge with genuinely
 * nothing recorded for them), not "Unknown" -- only a bridge that can't
 * be reached at all degrades.
 */
export interface EbpfHostStatus {
  hostname: string;
  role: string;
  tone: ReturnType<typeof securityPillTone>;
  alertCount: number;
}

export function buildEbpfHostStatuses(findings: SecurityFinding[]): EbpfHostStatus[] {
  return findings.map((f) => ({
    hostname: f.hostname,
    role: f.role,
    tone: securityPillTone(f.raw_data.ebpf_signal),
    alertCount: f.raw_data.ebpf_signal.alerts?.length ?? 0,
  }));
}

// --- Phase Sec-5a (exposed-ports page): confirmed-exposure vocabulary ----
//
// Unlike CVE severity / eBPF priority, exposed-port mismatches don't carry
// their own severity string from the backend -- every entry here already
// represents the same thing (a world-open rule confirmed backed by a real
// listening socket), so there's no ranking to apply, just a flat list.

/** One row per (host, confirmed exposed port) -- the flat shape
 * /security/exposed-ports' table renders, straight off
 * AgentExposedPortSignal.mismatches (nodes/security.py's
 * `_check_exposed_ports`, services/exposed_ports.py) plus the host each
 * mismatch came from. A restricted (viewer) or degraded (listening-port
 * collector unreachable) signal contributes no rows here by construction
 * -- both have `mismatches` undefined, never a fabricated empty-but-clean
 * list -- see buildExposedPortHostStatuses below for how those hosts are
 * still represented instead of silently vanishing.
 */
export interface ExposedPortRow extends AgentExposedPortMismatch {
  hostname: string;
  role: string;
}

export function flattenExposedPortFindings(findings: SecurityFinding[]): ExposedPortRow[] {
  const rows: ExposedPortRow[] = [];
  for (const f of findings) {
    for (const mismatch of f.raw_data.exposed_port_signal.mismatches ?? []) {
      rows.push({ ...mismatch, hostname: f.hostname, role: f.role });
    }
  }
  return rows.sort((a, b) => a.hostname.localeCompare(b.hostname) || a.port - b.port);
}

/** Every monitored host's exposed-port status, independent of whether it
 * actually has a confirmed mismatch right now -- same reasoning
 * buildCveHostStatuses' own docstring gives: a flat mismatches table on
 * its own would only ever show hosts *with* a finding, making a clean
 * host (checked, nothing confirmed reachable), a degraded one (no
 * listening-port collector reachable for this host -- "unknown", not
 * "clean"), and a restricted one (viewer role) indistinguishable from a
 * host nobody's watching at all.
 */
export interface ExposedPortHostStatus {
  hostname: string;
  role: string;
  tone: ReturnType<typeof securityPillTone>;
  mismatchCount: number;
  listeningCount: number;
}

export function buildExposedPortHostStatuses(findings: SecurityFinding[]): ExposedPortHostStatus[] {
  return findings.map((f) => ({
    hostname: f.hostname,
    role: f.role,
    tone: securityPillTone(f.raw_data.exposed_port_signal),
    mismatchCount: f.raw_data.exposed_port_signal.mismatches?.length ?? 0,
    listeningCount: f.raw_data.exposed_port_signal.listening_ports?.length ?? 0,
  }));
}

/** One row per (host, correlated auth-failure log line) -- the flat shape
 * /security/auth-activity's table renders, straight off
 * AgentAuthAnomalySignal.entries (nodes/security.py's
 * `_check_auth_anomaly`) plus the host each entry came from. Capped at
 * whatever `entries` itself already is -- that sub-check only ever
 * returns its 5 most recent matches even when more exist in the lookback
 * window (see its own docstring), so this is "5 most recent per host",
 * not "every match"; `detail` on the underlying signal (surfaced via
 * buildAuthHostStatuses below) is what carries the true total count.
 */
export interface AuthLogRow {
  hostname: string;
  role: string;
  ts: number;
  line: string;
  service: string | null;
}

export function flattenAuthFindings(findings: SecurityFinding[]): AuthLogRow[] {
  const rows: AuthLogRow[] = [];
  for (const f of findings) {
    for (const entry of f.raw_data.auth_signal.entries ?? []) {
      rows.push({ hostname: f.hostname, role: f.role, ts: entry.ts, line: entry.line, service: entry.service });
    }
  }
  return rows.sort((a, b) => b.ts - a.ts);
}

/** Every monitored host's auth-anomaly status, independent of whether it
 * actually has a flagged entry right now -- same reasoning
 * buildCveHostStatuses' own docstring gives: a flat entries table on its
 * own would only ever show hosts *with* a finding, making a clean,
 * degraded (Loki didn't respond), or restricted (viewer role) host
 * indistinguishable from one that was never checked at all.
 */
export interface AuthHostStatus {
  hostname: string;
  role: string;
  tone: ReturnType<typeof securityPillTone>;
  entryCount: number;
  detail?: string;
}

export function buildAuthHostStatuses(findings: SecurityFinding[]): AuthHostStatus[] {
  return findings.map((f) => ({
    hostname: f.hostname,
    role: f.role,
    tone: securityPillTone(f.raw_data.auth_signal),
    entryCount: f.raw_data.auth_signal.entries?.length ?? 0,
    detail: f.raw_data.auth_signal.detail,
  }));
}

// --- Phase Sec-6 add-on: top-of-page insight summary ---------------------
//
// Everything below is derived strictly from fields already on the wire
// (AuthHostStatus.detail/entryCount, AuthLogRow.line/service/ts) -- no new
// endpoint, no numbers invented. The point is to turn "here's a table, go
// read it" into "here's what's actually going on", the same way a human
// triaging this page would summarize it to a teammate.

// `_check_auth_anomaly` (nodes/security.py) always writes its `detail`
// string starting "<N> correlated auth-failure entr{y,ies} for <host> in
// the last 60 minutes, most recent: ...". The table only ever renders the
// 5 most recent matches (`entries: top = entries[:5]`), so a host with
// more than 5 real matches would otherwise silently look identical to one
// with exactly 5 -- this recovers the true count from the sentence the
// backend already computed it into, rather than duplicating that logic.
function parseTrueAuthCount(detail: string | undefined, fallback: number): number {
  const m = detail?.match(/^(\d+)\s+correlated/);
  return m ? parseInt(m[1], 10) : fallback;
}

// Mirrors _AUTH_LOG_SIGNAL_PATTERN's own vocabulary (nodes/security.py) --
// sudo checked first since a failed `sudo` attempt is logged via PAM as
// "authentication failure", which would otherwise also match the generic
// catch-all and get mislabeled as a plain login failure instead of a
// privilege-escalation attempt.
const AUTH_LINE_CATEGORIES: { label: string; re: RegExp }[] = [
  { label: "repeated sudo failures", re: /\bsudo\b/i },
  { label: "invalid-user probes", re: /invalid user/i },
  { label: "failed-password attempts", re: /failed password/i },
  { label: "permission-denied errors", re: /permission denied/i },
];

function classifyAuthLine(line: string): string {
  for (const { label, re } of AUTH_LINE_CATEGORIES) {
    if (re.test(line)) return label;
  }
  return "other authentication failures";
}

function timeAgo(ts: number, now: number): string {
  const minutes = Math.max(0, Math.round((now - ts) / 60000));
  if (minutes < 1) return "just now";
  if (minutes === 1) return "1 minute ago";
  if (minutes < 60) return `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  return `${hours} hour${hours === 1 ? "" : "s"} ago`;
}

export interface AuthInsightHost {
  hostname: string;
  role: string;
  count: number;
  cappedDisplay: boolean; // true count exceeds the 5 rows actually shown for this host
  topCategory: string | null;
  mostRecentTs: number | null;
}

export interface AuthInsight {
  tone: "critical" | "warning" | "clean";
  headline: string;
  bullets: string[];
  hosts: AuthInsightHost[];
  totalFlaggedEntries: number;
  categoryBreakdown: { label: string; count: number }[];
}

/** The single narrative summary /security/auth-activity renders at the
 * top of the page -- which host(s), what kind of activity, how much, and
 * how fresh, plus what couldn't be checked at all. Returns null only when
 * there are no monitored hosts to say anything about (mirrors the page's
 * own "No monitored nodes yet" branch).
 */
export function buildAuthInsight(hostStatuses: AuthHostStatus[], rows: AuthLogRow[]): AuthInsight | null {
  if (hostStatuses.length === 0) return null;

  const now = Date.now();
  const flagged = hostStatuses.filter((h) => h.tone.label === "Flagged");
  const degraded = hostStatuses.filter((h) => h.tone.label === "Unknown");
  const restricted = hostStatuses.filter((h) => h.tone.label === "Admin only");

  const rowsByHost = new Map<string, AuthLogRow[]>();
  const categoryCounts = new Map<string, number>();
  for (const row of rows) {
    if (!rowsByHost.has(row.hostname)) rowsByHost.set(row.hostname, []);
    rowsByHost.get(row.hostname)!.push(row);
    const category = classifyAuthLine(row.line);
    categoryCounts.set(category, (categoryCounts.get(category) ?? 0) + 1);
  }

  const hosts: AuthInsightHost[] = flagged
    .map((h) => {
      const hostRows = rowsByHost.get(h.hostname) ?? [];
      const count = parseTrueAuthCount(h.detail, h.entryCount);

      const perHostCategoryCounts = new Map<string, number>();
      for (const row of hostRows) {
        const c = classifyAuthLine(row.line);
        perHostCategoryCounts.set(c, (perHostCategoryCounts.get(c) ?? 0) + 1);
      }
      let topCategory: string | null = null;
      let topCategoryCount = 0;
      for (const [c, n] of perHostCategoryCounts) {
        if (n > topCategoryCount) {
          topCategory = c;
          topCategoryCount = n;
        }
      }

      return {
        hostname: h.hostname,
        role: h.role,
        count,
        cappedDisplay: count > hostRows.length,
        topCategory,
        mostRecentTs: hostRows[0]?.ts ?? null,
      };
    })
    .sort((a, b) => b.count - a.count);

  const totalFlaggedEntries = hosts.reduce((sum, h) => sum + h.count, 0);
  const categoryBreakdown = Array.from(categoryCounts.entries())
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count);

  const tone: AuthInsight["tone"] = flagged.length > 0 ? "critical" : degraded.length > 0 ? "warning" : "clean";

  let headline: string;
  if (flagged.length === 0) {
    headline =
      degraded.length > 0
        ? `No confirmed auth-failure activity, but ${degraded.length} host${
            degraded.length === 1 ? "" : "s"
          } couldn't be checked this pass -- that's unknown, not clean.`
        : `All ${hostStatuses.length} monitored host${hostStatuses.length === 1 ? "" : "s"} clean -- no correlated auth-failure activity in the last 60 minutes.`;
  } else {
    const worst = hosts[0];
    const worstCategory = worst.topCategory ?? categoryBreakdown[0]?.label ?? "auth failures";
    const spread = flagged.length > 1 ? `, ${flagged.length - 1} other host${flagged.length - 1 === 1 ? "" : "s"} also flagged` : "";
    headline = `${flagged.length} of ${hostStatuses.length} host${
      hostStatuses.length === 1 ? "" : "s"
    } show correlated auth-failure activity -- heaviest on ${worst.hostname} (${worst.count} ${
      worst.count === 1 ? "entry" : "entries"
    }, mostly ${worstCategory})${spread}.`;
  }

  const bullets: string[] = [];
  for (const h of hosts) {
    const recency = h.mostRecentTs ? `, most recent ${timeAgo(h.mostRecentTs, now)}` : "";
    const category = h.topCategory ? ` -- mostly ${h.topCategory}` : "";
    const capNote = h.cappedDisplay ? " (table shows 5 most recent)" : "";
    bullets.push(`${h.hostname} (${h.role}): ${h.count} ${h.count === 1 ? "entry" : "entries"}${category}${recency}${capNote}`);
  }
  if (degraded.length > 0) {
    bullets.push(
      `Could not check: ${degraded.map((h) => h.hostname).join(", ")} -- the log store didn't respond in time, treated as unknown rather than clean.`,
    );
  }
  if (restricted.length > 0) {
    bullets.push(`${restricted.length} host${restricted.length === 1 ? "" : "s"} restricted to admin accounts for this view.`);
  }

  return { tone, headline, bullets, hosts, totalFlaggedEntries, categoryBreakdown };
}
