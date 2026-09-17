import type { LucideIcon } from "lucide-react";
import { Server, Boxes, KeyRound } from "lucide-react";

/** Phase 0 (security scope-clarification roadmap): every Security
 * category card and every `/security/*` sub-page states, in one of these
 * three fixed terms, which of OpenStack's three layers its finding
 * actually reads from -- the physical/virtual host OpenStack itself runs
 * on ("Node"), the VM instances hosted on top of it ("Instance", always
 * rolled up per the node currently hosting them since Cortex has no
 * standalone instance-scoped entry point yet -- see Phase Sec-7), or
 * Keystone's identity plane ("Identity"), which is neither a node nor an
 * instance. This is the single highest-value, zero-risk change the
 * scope-clarification roadmap calls for: it costs nothing and it's the
 * literal answer to "is this about the node or the VM" that prompted the
 * roadmap in the first place. See that roadmap's §2 master table for the
 * per-check scope assignment this tag set is drawn from.
 */
export type SecurityScope = "node" | "instance" | "identity";

export const SECURITY_SCOPE_LABEL: Record<SecurityScope, string> = {
  node: "Scope: Node",
  instance: "Scope: Instance (rolled up by node)",
  identity: "Scope: Identity",
};

// One line of "what that actually means" for a reader who hasn't read the
// roadmap doc -- shown as this badge's title/tooltip so the distinction is
// available without leaving the page.
const SECURITY_SCOPE_HINT: Record<SecurityScope, string> = {
  node: "Reads the controller/compute/storage/monitoring host itself -- its OS, its logs, its kernel -- not any guest VM's own OS.",
  instance: "Reads Neutron/Nova data belonging to a VM's own port or security group, rolled up under whichever node currently hosts that VM.",
  identity: "Reads Keystone -- users, projects, tokens -- which isn't a host or a VM but a service every other layer authenticates against.",
};

const SECURITY_SCOPE_ICON: Record<SecurityScope, LucideIcon> = {
  node: Server,
  instance: Boxes,
  identity: KeyRound,
};

export default function SecurityScopeTag({
  scope,
  className = "",
}: {
  scope: SecurityScope;
  className?: string;
}) {
  const Icon = SECURITY_SCOPE_ICON[scope];
  return (
    <span
      title={SECURITY_SCOPE_HINT[scope]}
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.04em] text-text-faint ${className}`}
      style={{ border: "1px solid var(--border-soft)", background: "var(--canvas)" }}
    >
      <Icon className="h-2.5 w-2.5 shrink-0" strokeWidth={2} aria-hidden="true" />
      {SECURITY_SCOPE_LABEL[scope]}
    </span>
  );
}
