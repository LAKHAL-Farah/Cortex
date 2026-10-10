"""Executes an *approved* remediation, through the OpenStack SDK or Ansible and
nothing else (roadmap 4.4, adr-0015).

This module knows how to turn one proposal command into a typed action and run
it. It does **not** decide whether it may: approval, the explicit click, the
role check, expiry and the stale-simulation re-check all live in
services/remediation_approval.py (`start_execution`), and that is the only
caller. Nothing here is reachable from the agent graph, a scheduler or an
unauthenticated route -- Confidence Ladder Level 0: Cortex recommends, a person
approves, a person clicks, and only then does anything run.

Why a plan, not a shell
-----------------------
The proposal's `command` is text meant for a human to read and paste. Running
it through a shell would make every metacharacter in a catalog command
(`&&`, `;`, `$(...)`, a trailing "(then, if ...)" note) an execution
primitive. Instead `plan_for` *parses* the command against a short, explicit
allow-list and produces an `ExecutionPlan` of validated fields (an operation
name, a host, identifiers matched against strict patterns). The backends only
ever receive those fields:

- **OpenStack SDK** (`openstack_sdk`): API-side changes -- enable/disable a
  compute service, soft/hard reboot one guest, enable a network agent or port.
  Uses a *separate* cloud profile (`OS_REMEDIATION_CLOUD`, default
  `cortex-operator`) from the read-only `cortex-reader` the rest of Cortex
  runs on, so the discovery/monitoring path can never change anything even
  if this module were bypassed.
- **Ansible** (`ansible`): on-host changes -- `docker restart|stop|start` and
  `systemctl restart|stop|start` -- through `playbook-remediate.yml`, with the
  values passed as an extra-vars JSON file (never interpolated into a command
  line) and the run limited to the one host.

Anything else -- deletes, rebuilds, prunes, `kill <pid>`, compound commands,
commands with an unresolved `<placeholder>` -- is `NotExecutable`: the
proposal stays approvable (a person may run it by hand, as before) but the UI
offers no Execute button for it. The list is deliberately small; widening it
is a code change with a review, not a configuration switch.

`execution_mode()` is the kill switch: `off` makes every execution request a
503 regardless of approvals.
"""
import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import openstack

from .ansible_runner import ANSIBLE_DIR, CORTEX_ENV

logger = logging.getLogger(__name__)

# The identity Cortex itself uses for the *read-only* collectors is
# `cortex-reader` (OS_CLOUD). Changing anything needs its own, narrower
# credentials in clouds.yaml.
OS_REMEDIATION_CLOUD = os.environ.get("OS_REMEDIATION_CLOUD", "cortex-operator")

ANSIBLE_TIMEOUT_SECONDS = int(os.environ.get("CORTEX_REMEDIATION_ANSIBLE_TIMEOUT", "180"))
REMEDIATE_PLAYBOOK = "playbook-remediate.yml"
OUTPUT_TAIL_CHARS = 1500

ExecutionMode = Literal["off", "live"]


def execution_mode() -> ExecutionMode:
    """`CORTEX_REMEDIATION_EXECUTION=off|live`. Unset means `live` everywhere
    except production, where it must be turned on on purpose -- an approval
    flow is not a reason for a production stack to start changing things the
    day it is deployed."""
    raw = (os.environ.get("CORTEX_REMEDIATION_EXECUTION") or "").strip().lower()
    if raw in ("off", "live"):
        return raw  # type: ignore[return-value]
    return "off" if CORTEX_ENV == "production" else "live"


# --------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------

class NotExecutable(Exception):
    """This command will not be run by Cortex. `reason` is safe to show."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ExecutionPlan:
    backend: Literal["openstack_sdk", "ansible"]
    operation: str  # e.g. "compute_service.disable", "container.restart"
    host: Optional[str]  # the node it lands on (Ansible target / service host)
    targets: tuple[str, ...]  # container names, unit names, instance/agent/port ids
    params: dict = field(default_factory=dict, hash=False, compare=False)
    summary: str = ""  # plain language, shown on the confirm step

    def as_dict(self) -> dict:
        return {
            "backend": self.backend,
            "operation": self.operation,
            "host": self.host,
            "targets": list(self.targets),
            "params": dict(self.params),
            "summary": self.summary,
        }


_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@_.:-]{0,63}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_PLACEHOLDER_RE = re.compile(r"<[A-Za-z_][\w\-]*>")
_SHELL_META_RE = re.compile(r"(&&|\|\||[;|`<>\n]|\$\(|\$\{)")

# A unit whose restart would cut off the very channel (SSH) or runtime
# (docker, networking) Cortex uses to reach the host, or take every guest down.
_PROTECTED_UNITS = {
    "ssh", "sshd", "docker", "containerd", "networking", "network", "networkmanager",
    "systemd-networkd", "systemd-resolved", "libvirtd", "firewalld", "ufw",
}
_MAX_CONTAINERS = 4

# Why a command is refused, by kind -- worded for a person standing at the
# approve screen, not for a log.
_DESTRUCTIVE_RE = re.compile(r"\b(delete|rebuild|prune|purge|wipe|evacuate|reset-state|migrate|rm|image\s+save)\b", re.I)


# `--limit` accepts group names and these two reserved patterns as well as
# hosts: `all` would run the playbook on the whole inventory. They are refused
# here, and the playbook independently insists on exactly one host.
_RESERVED_HOST_PATTERNS = {"all", "ungrouped", "localhost"}


def _is_ok_host(host: Optional[str]) -> bool:
    return bool(host) and bool(_HOST_RE.match(host)) and host.lower() not in _RESERVED_HOST_PATTERNS  # type: ignore[union-attr,arg-type]


def plan_for(command: str, host: Optional[str]) -> ExecutionPlan:
    """The `ExecutionPlan` for `command`, or `NotExecutable`. Pure -- no I/O --
    so the UI can ask "would this run, and what exactly?" before anyone clicks.
    """
    text = (command or "").strip()
    if not text:
        raise NotExecutable("The proposal has no command.")
    if _PLACEHOLDER_RE.search(text):
        raise NotExecutable("The command still has a value to fill in; Cortex will not guess it.")
    if _SHELL_META_RE.search(text) or "(" in text:
        raise NotExecutable(
            "This is more than one command (or has a note inside it). Cortex only runs a single, "
            "plain command -- run it by hand."
        )
    try:
        tokens = shlex.split(text)
    except ValueError:
        raise NotExecutable("The command could not be read safely.") from None
    if tokens and tokens[0] == "sudo":
        tokens = tokens[1:]
    if not tokens:
        raise NotExecutable("The proposal has no command.")

    if tokens[0] == "openstack":
        return _plan_openstack(tokens, text)
    if tokens[0] == "docker":
        return _plan_docker(tokens, host)
    if tokens[0] == "systemctl":
        return _plan_systemctl(tokens, host)

    if _DESTRUCTIVE_RE.search(text):
        raise NotExecutable("This removes or rewrites data. Cortex does not run those; run it by hand if you are sure.")
    raise NotExecutable("Cortex has no automated way to run this command; run it by hand.")


def _plan_openstack(tokens: list[str], text: str) -> ExecutionPlan:
    rest = tokens[1:]

    # openstack compute service set (--enable | --disable [--disable-reason R]) <host> nova-compute
    if rest[:3] == ["compute", "service", "set"]:
        flags, positionals, reason = _split_flags(rest[3:], value_flags={"--disable-reason"})
        enable, disable = "--enable" in flags, "--disable" in flags
        if enable == disable or set(flags) - {"--enable", "--disable"}:
            raise NotExecutable("Only a plain --enable or --disable (with an optional reason) can be run.")
        if len(positionals) != 2 or positionals[1] != "nova-compute" or not _is_ok_host(positionals[0]):
            raise NotExecutable("Only `nova-compute` on a named host can be enabled or disabled from here.")
        if reason is not None and (len(reason) > 200 or "\n" in reason):
            raise NotExecutable("The disable reason is too long.")
        host = positionals[0]
        verb = "disable" if disable else "enable"
        params = {"binary": "nova-compute"}
        if verb == "disable":
            params["reason"] = reason
        summary = (
            f"Stop new instances being scheduled on {host} (nova-compute disabled; running instances are not touched)."
            if verb == "disable"
            else f"Let new instances be scheduled on {host} again (nova-compute enabled)."
        )
        return ExecutionPlan("openstack_sdk", f"compute_service.{verb}", host, ("nova-compute",), params, summary)

    # openstack server reboot [--hard|--soft] <uuid>
    if rest[:2] == ["server", "reboot"]:
        flags, positionals, _ = _split_flags(rest[2:])
        if set(flags) - {"--hard", "--soft"} or len(flags) > 1:
            raise NotExecutable("Only a plain, --soft or --hard reboot can be run.")
        if len(positionals) != 1 or not _UUID_RE.match(positionals[0]):
            raise NotExecutable("A guest reboot needs the instance's full id.")
        kind = "HARD" if "--hard" in flags else "SOFT"
        return ExecutionPlan(
            "openstack_sdk", "server.reboot", None, (positionals[0],), {"reboot_type": kind},
            f"{kind.title()}-reboot instance {positionals[0]} (the guest restarts; its disk is kept).",
        )

    # openstack network agent set --enable <uuid>
    if rest[:3] == ["network", "agent", "set"]:
        flags, positionals, _ = _split_flags(rest[3:])
        if flags != ["--enable"] or len(positionals) != 1 or not _UUID_RE.match(positionals[0]):
            raise NotExecutable("Only `--enable` on one agent id can be run.")
        return ExecutionPlan(
            "openstack_sdk", "network_agent.enable", None, (positionals[0],), {},
            f"Set network agent {positionals[0]} administratively up.",
        )

    # openstack port set --enable <uuid>
    if rest[:2] == ["port", "set"]:
        flags, positionals, _ = _split_flags(rest[2:])
        if flags != ["--enable"] or len(positionals) != 1 or not _UUID_RE.match(positionals[0]):
            raise NotExecutable("Only `--enable` on one port id can be run.")
        return ExecutionPlan(
            "openstack_sdk", "port.enable", None, (positionals[0],), {},
            f"Set port {positionals[0]} administratively up.",
        )

    if _DESTRUCTIVE_RE.search(text):
        raise NotExecutable("This deletes, rebuilds or moves workloads. Cortex does not run those; run it by hand if you are sure.")
    raise NotExecutable("Cortex has no automated way to run this OpenStack command; run it by hand.")


def _plan_docker(tokens: list[str], host: Optional[str]) -> ExecutionPlan:
    if _DESTRUCTIVE_RE.search(" ".join(tokens)):
        raise NotExecutable("This removes data (images, containers or volumes). Cortex does not run those; run it by hand if you are sure.")
    if len(tokens) < 3 or tokens[1] not in ("restart", "stop", "start"):
        raise NotExecutable("Only `docker restart`, `stop` and `start` on named containers can be run.")
    names = tokens[2:]
    if any(n.startswith("-") for n in names) or not all(_NAME_RE.match(n) for n in names) or len(names) > _MAX_CONTAINERS:
        raise NotExecutable("Container names must be plain names (no options), and at most a few at a time.")
    if not _is_ok_host(host):
        raise NotExecutable("This command runs on a specific host, and the proposal does not name one.")
    verb = tokens[1]
    return ExecutionPlan(
        "ansible", f"container.{verb}", host, tuple(names), {},
        f"{verb.title()} container{'s' if len(names) > 1 else ''} {', '.join(names)} on {host}.",
    )


def _plan_systemctl(tokens: list[str], host: Optional[str]) -> ExecutionPlan:
    if len(tokens) != 3 or tokens[1] not in ("restart", "stop", "start"):
        raise NotExecutable("Only `systemctl restart`, `stop` and `start` on one unit can be run.")
    unit = tokens[2]
    if not _UNIT_RE.match(unit):
        raise NotExecutable("The unit name is not a plain name.")
    if unit.lower().removesuffix(".service") in _PROTECTED_UNITS:
        raise NotExecutable(f"`{unit}` is the connection or runtime Cortex itself depends on; run it by hand.")
    if not _is_ok_host(host):
        raise NotExecutable("This command runs on a specific host, and the proposal does not name one.")
    verb = tokens[1]
    return ExecutionPlan("ansible", f"unit.{verb}", host, (unit,), {}, f"{verb.title()} systemd unit {unit} on {host}.")


def _split_flags(args: list[str], value_flags: frozenset[str] | set[str] = frozenset()):
    """(flags, positionals, value of the last value-flag). `--x=y` is refused
    rather than interpreted -- the catalog never writes it."""
    flags: list[str] = []
    positionals: list[str] = []
    value: Optional[str] = None
    it = iter(args)
    for tok in it:
        if tok in value_flags:
            value = next(it, None)
            if value is None:
                raise NotExecutable("A flag is missing its value.")
        elif tok.startswith("-"):
            if "=" in tok:
                raise NotExecutable("Flags written as --name=value are not run.")
            flags.append(tok)
        else:
            positionals.append(tok)
    return flags, positionals, value


# --------------------------------------------------------------------
# Running
# --------------------------------------------------------------------

@dataclass
class ExecutionResult:
    ok: bool
    backend: str
    operation: str
    summary: str  # one line, safe to show
    output: str = ""  # tail of stdout/stderr, for the audit entry
    duration_ms: int = 0
    error: Optional[str] = None

    def as_details(self, plan: ExecutionPlan) -> dict:
        return {
            "execution": {
                **plan.as_dict(),
                "ok": self.ok,
                "result": self.summary,
                "error": self.error,
                "output_tail": self.output[-OUTPUT_TAIL_CHARS:],
                "duration_ms": self.duration_ms,
            }
        }


def _connect():
    """Separate from topology_sync._connect on purpose: this one uses the
    credentials that may change things. A test monkeypatches it."""
    return openstack.connect(cloud=OS_REMEDIATION_CLOUD)


def run_plan(plan: ExecutionPlan) -> ExecutionResult:
    """Run one plan and report what happened. Never raises: a backend failure
    is a result (`ok=False`), because the caller must record *that* in the audit
    trail, not lose it to a 500."""
    started = time.monotonic()
    try:
        if plan.backend == "openstack_sdk":
            result = _run_sdk(plan)
        elif plan.backend == "ansible":
            result = _run_ansible(plan)
        else:  # pragma: no cover -- ExecutionPlan only builds the two above
            result = ExecutionResult(False, plan.backend, plan.operation, "Unknown backend.", error="unknown backend")
    except Exception as exc:  # noqa: BLE001
        logger.exception("remediation: executing %s failed", plan.operation)
        result = ExecutionResult(
            False, plan.backend, plan.operation, "The action failed before it could finish.",
            error=f"{type(exc).__name__}: {exc}"[:500],
        )
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


def _run_sdk(plan: ExecutionPlan) -> ExecutionResult:
    conn = _connect()
    op = plan.operation
    target = plan.targets[0]

    if op in ("compute_service.disable", "compute_service.enable"):
        # The query filters host/binary server-side on a real Nova, but not
        # every endpoint honours them (the sandbox's simulator returns every
        # service). Taking the first row of an unfiltered list would act on the
        # wrong host, so match here as well and insist on exactly one.
        services = [
            svc for svc in conn.compute.services(host=plan.host, binary=plan.params["binary"])
            if getattr(svc, "host", None) == plan.host and getattr(svc, "binary", None) == plan.params["binary"]
        ]
        if not services:
            return _sdk_fail(plan, f"No {plan.params['binary']} service is registered on {plan.host}.")
        if len(services) > 1:
            return _sdk_fail(plan, f"{len(services)} {plan.params['binary']} services match {plan.host}; refusing to guess which.")
        service = services[0]
        if op.endswith("disable"):
            conn.compute.disable_service(
                service, host=plan.host, binary=plan.params["binary"], disabled_reason=plan.params.get("reason")
            )
            return _sdk_ok(plan, f"nova-compute on {plan.host} is now disabled for scheduling.")
        conn.compute.enable_service(service, host=plan.host, binary=plan.params["binary"])
        return _sdk_ok(plan, f"nova-compute on {plan.host} is now enabled for scheduling.")

    if op == "server.reboot":
        conn.compute.reboot_server(target, plan.params["reboot_type"])
        return _sdk_ok(plan, f"Reboot ({plan.params['reboot_type'].lower()}) requested for instance {target}.")

    if op == "network_agent.enable":
        conn.network.update_agent(target, is_admin_state_up=True)
        return _sdk_ok(plan, f"Network agent {target} set administratively up.")

    if op == "port.enable":
        conn.network.update_port(target, is_admin_state_up=True)
        return _sdk_ok(plan, f"Port {target} set administratively up.")

    return _sdk_fail(plan, f"No SDK handler for {op}.")


def _sdk_ok(plan: ExecutionPlan, summary: str) -> ExecutionResult:
    return ExecutionResult(True, plan.backend, plan.operation, summary)


def _sdk_fail(plan: ExecutionPlan, message: str) -> ExecutionResult:
    return ExecutionResult(False, plan.backend, plan.operation, message, error=message)


_RECAP_RE = (
    r"^{host}\s*:\s*ok=(?P<ok>\d+)\s+changed=(?P<changed>\d+)\s+unreachable=(?P<unreachable>\d+)\s+failed=(?P<failed>\d+)"
)


def _playbook_path() -> Path:
    path = ANSIBLE_DIR / REMEDIATE_PLAYBOOK
    if not path.exists():
        raise FileNotFoundError(f"{REMEDIATE_PLAYBOOK} not found in {ANSIBLE_DIR}")
    return path


def _run_ansible(plan: ExecutionPlan) -> ExecutionResult:
    kind, _, action = plan.operation.partition(".")
    extra_vars = {
        "remediation_kind": kind,
        "remediation_action": action,
        "remediation_targets": list(plan.targets),
        "remediation_host": plan.host,
    }
    fd, name = tempfile.mkstemp(prefix="cortex-remediate-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:  # mkstemp creates it 0600
            json.dump(extra_vars, fh)
        argv = [
            "ansible-playbook", str(_playbook_path()),
            "-i", str(ANSIBLE_DIR / "inventory" / "hosts.ini"),
            "--limit", plan.host or "",
            "-e", f"@{name}",
        ]
        proc = subprocess.run(argv, cwd=ANSIBLE_DIR, capture_output=True, text=True, timeout=ANSIBLE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return ExecutionResult(
            False, plan.backend, plan.operation,
            f"Ansible did not finish within {ANSIBLE_TIMEOUT_SECONDS} seconds; the host may be partly changed.",
            error="timeout",
        )
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass

    output = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
    recap = re.search(_RECAP_RE.format(host=re.escape(plan.host or "")), proc.stdout or "", re.M)
    # rc 0 alone is not enough: a --limit that matches nothing can exit 0 with
    # no host in the recap, and that must not read as "it worked".
    if proc.returncode == 0 and recap and int(recap["failed"]) == 0 and int(recap["unreachable"]) == 0:
        return ExecutionResult(True, plan.backend, plan.operation, plan.summary.rstrip(".") + " -- done.", output=output[-OUTPUT_TAIL_CHARS:])
    no_such_host = re.search(r"no hosts to target|Could not match supplied host pattern|does not match any hosts", output)
    if recap and int(recap["unreachable"]):
        reason = f"{plan.host} was unreachable over SSH."
    elif not recap and (no_such_host or proc.returncode == 0):
        reason = f"{plan.host} is not in the Ansible inventory, so nothing ran."
    else:
        reason = f"Ansible reported a failure on {plan.host} (exit code {proc.returncode})."
    return ExecutionResult(False, plan.backend, plan.operation, reason, output=output[-OUTPUT_TAIL_CHARS:], error=reason)
