import os
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def _default_ansible_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "infra" / "ansible"
        if candidate.exists():
            return candidate
    raise RuntimeError(
        "Could not locate infra/ansible; set CORTEX_ANSIBLE_DIR explicitly."
    )

# development (default) | sandbox | production. Set explicitly by the
# CORTEX_ENV env var in each infra/docker-compose*.yml -- used only to catch
# the sandbox-inventory-in-production misconfiguration below, nothing else
# branches on it.
CORTEX_ENV = os.environ.get("CORTEX_ENV", "development").strip().lower()

ANSIBLE_DIR = Path(os.environ.get("CORTEX_ANSIBLE_DIR") or _default_ansible_dir())
SITE_PLAYBOOK = ANSIBLE_DIR / "site.yml"


def _guard_against_sandbox_in_production(path: Path) -> None:
    """Fail loudly at startup instead of silently running Ansible (and
    writing newly-registered nodes) against infra/ansible-sandbox -- the
    local docker-simulated inventory (controller-sim/compute1-sim/... on
    10.0.1.20+) -- when this is supposed to be the real production stack.

    This is exactly the failure mode where nodes added through the UI in
    prod land in ansible-sandbox/inventory/hosts.ini instead of
    ansible/inventory/hosts.ini: usually CORTEX_ANSIBLE_HOST_DIR is set
    wrong in the prod .env, or docker-compose.sandbox.yml (which overrides
    CORTEX_ANSIBLE_HOST_DIR to /infra/ansible-sandbox) got layered on top
    of docker-compose.prod.yml instead of docker-compose.yml.
    """
    if CORTEX_ENV == "production" and "ansible-sandbox" in path.parts:
        raise RuntimeError(
            f"CORTEX_ENV=production but the resolved Ansible directory is "
            f"'{path}', which contains 'ansible-sandbox'. Refusing to start "
            f"rather than silently install/register nodes against the sandbox "
            f"inventory. Check CORTEX_ANSIBLE_DIR / CORTEX_ANSIBLE_HOST_DIR in "
            f"the production .env, and confirm the prod stack is being brought "
            f"up with docker-compose.prod.yml only (not docker-compose.yml + "
            f"docker-compose.sandbox.yml)."
        )


logger.info("ansible_runner: CORTEX_ENV=%s, ANSIBLE_DIR=%s", CORTEX_ENV, ANSIBLE_DIR)
_guard_against_sandbox_in_production(ANSIBLE_DIR)

def install_node_exporter(hostname: str) -> bool:
    try:
        result = subprocess.run(
            [
                "ansible-playbook",
                str(SITE_PLAYBOOK),
                "--limit", hostname,
                # This endpoint only ever installs node_exporter. Without this, registering
                # controller-sim (a member of both `monitoring` and `prometheus_server`) also
                # runs the prometheus play against it -- a heavyweight, internet-dependent
                # install that duplicates the Prometheus already running as its own container
                # in the sandbox, and whose failure silently aborted file_sd regeneration too.
                "--skip-tags", "prometheus_server_install",
                "-i", str(ANSIBLE_DIR / "inventory" / "hosts.ini"),
            ],
            cwd=ANSIBLE_DIR,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            logger.error("Ansible failed for %s: %s", hostname, result.stderr)
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("Ansible timed out for %s", hostname)
        return False