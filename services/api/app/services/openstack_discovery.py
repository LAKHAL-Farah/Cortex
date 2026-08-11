import openstack
from sqlalchemy.orm import Session
from .. import crud, schemas
from .inventory_manager import add_host_to_inventory
from .ansible_runner import install_node_exporter
from .prometheus_sd import regenerate_file_sd

def discover_new_computes(db: Session) -> None:
    conn = openstack.connect(cloud="admin")
    hypervisors = conn.compute.hypervisors()

    existing = {n.hostname for n in crud.list_nodes(db)}

    for hv in hypervisors:
        hostname = hv.name
        if hostname in existing:
            continue

        node = crud.create_node(db, schemas.NodeCreate(
            hostname=hostname,
            ip_address=hv.host_ip,
            role="compute",
        ))

        add_host_to_inventory(node.hostname, node.ip_address, "compute")
        if install_node_exporter(node.hostname):
            regenerate_file_sd(db)