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



def remove_host_from_inventory(hostname: str) -> None:
    """Strip every inventory line for this host, from every group it's in.

    Matches on the leading token only (the hostname column), so this also
    cleans up any duplicate/stray lines for the same host across groups --
    not just the single line add_host_to_inventory would have written.
    """
    content = INVENTORY_PATH.read_text()
    kept_lines = []
    removed = False
    for raw_line in content.splitlines(keepends=True):
        stripped = raw_line.strip()
        first_token = stripped.split(" ", 1)[0] if stripped else ""
        if first_token == hostname and not stripped.startswith("["):
            removed = True
            continue
        kept_lines.append(raw_line)

    if removed:
        INVENTORY_PATH.write_text("".join(kept_lines))