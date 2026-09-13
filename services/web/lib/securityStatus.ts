import type { AgentCveMatch, AgentSecGroupInfo, AgentSecGroupRule, AgentSecGroupSignal, AgentSecurityData, AgentSecuritySignal, SecurityFinding } from "./types";

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
  const signals = [raw.auth_signal, raw.sec_group_signal, raw.cve_signal, raw.ebpf_signal];
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
