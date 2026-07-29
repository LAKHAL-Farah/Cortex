import os
from pathlib import Path
from .ansible_runner import ANSIBLE_DIR 


INVENTORY_PATH = Path(
    os.environ.get("CORTEX_INVENTORY_PATH") or (ANSIBLE_DIR / "inventory" / "hosts.ini")
)

GROUP_BY_ROLE = {
    "controller": "controllers",
    "compute": "computes",
    "storage": "storages",
    "monitoring": "monitoring",   # matches the [monitoring:children] group in hosts.ini
}

def add_host_to_inventory(hostname: str, ip: str, role: str) -> None:
    group = GROUP_BY_ROLE.get(role)
    if not group:
        raise ValueError(f"Role inconnu: {role}")

    content = INVENTORY_PATH.read_text()
    line = f"{hostname} ansible_host={ip} ansible_user=root node_role={role}\n"

    if line.strip() in content:
        return

    marker = f"[{group}]\n"
    if marker not in content:
        raise ValueError(f"Groupe [{group}] introuvable dans hosts.ini")

    idx = content.index(marker) + len(marker)
    new_content = content[:idx] + line + content[idx:]
    INVENTORY_PATH.write_text(new_content)