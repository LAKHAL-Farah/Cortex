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

ANSIBLE_DIR = Path(os.environ.get("CORTEX_ANSIBLE_DIR") or _default_ansible_dir())
SITE_PLAYBOOK = ANSIBLE_DIR / "site.yml"

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