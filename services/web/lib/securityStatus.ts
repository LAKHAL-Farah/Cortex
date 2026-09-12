import type { AgentSecuritySignal } from "./types";

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
