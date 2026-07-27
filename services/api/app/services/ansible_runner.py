import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ANSIBLE_DIR = Path(__file__).resolve().parents[4] / "infra" / "ansible"
SITE_PLAYBOOK = ANSIBLE_DIR / "site.yml"

def install_node_exporter(hostname: str) -> bool:
    try:
        result = subprocess.run(
            [
                "ansible-playbook",
                str(SITE_PLAYBOOK),
                "--limit", hostname,
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