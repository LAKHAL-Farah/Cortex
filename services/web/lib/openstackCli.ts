/** Shell preamble an operator needs before any `openstack ...` command works
 * on a Kolla-Ansible controller: admin credentials, then the Kolla virtualenv
 * that provides the `openstack` CLI. Override at build time with
 * NEXT_PUBLIC_OPENSTACK_CLI_PREAMBLE (set it to an empty string to disable).
 *
 * This is applied only to what the Copy button puts on the clipboard. The
 * command stored on a remediation proposal (and therefore its approval digest
 * and the executor's allow-list parse) is never modified.
 */
export const OPENSTACK_CLI_PREAMBLE: string =
  process.env.NEXT_PUBLIC_OPENSTACK_CLI_PREAMBLE ?? "source ~/admin-openrc.sh && source /opt/kolla-venv/bin/activate";

const OPENSTACK_CMD_RE = /^\s*(sudo\s+)?openstack(\s|$)/;

export function needsOpenstackPreamble(command: string): boolean {
  return Boolean(OPENSTACK_CLI_PREAMBLE) && OPENSTACK_CMD_RE.test(command);
}

/** `command` ready to paste into a controller shell. */
export function withOpenstackEnv(command: string): string {
  return needsOpenstackPreamble(command) ? `${OPENSTACK_CLI_PREAMBLE} && ${command.trim()}` : command;
}
