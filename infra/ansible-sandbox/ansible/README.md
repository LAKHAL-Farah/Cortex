# Monitoring — node_exporter + Prometheus (Ansible)

Automated deployment for the RIF SAS OpenStack infra, matching the same
inventory pattern already used for Kolla-Ansible (`/etc/kolla/multinode`).

## Layout

```
ansible-monitoring/
├── ansible.cfg
├── inventory/hosts.ini      # controller / compute1 / compute2 / storage + node_role per host
├── group_vars/all.yml       # versions, ports, scrape interval (20s)
├── site.yml                 # deploy playbook
├── verify.yml                # automated P0 checklist (1.2 + 1.3)
└── roles/
    ├── node_exporter/       # installed on every node
    └── prometheus/          # installed on controller only
```

## Prerequisites

- SSH key auth root→root from wherever you run Ansible to `controller`,
  `compute1`, `compute2`, `storage` (same as the OpenStack install prereqs).
- Target nodes have outbound internet on `eth0` to reach GitHub releases.
- Python 3 present on targets (default on Ubuntu 22.04).

Install Ansible locally if needed:

```bash
python3 -m venv /opt/ansible-venv
source /opt/ansible-venv/bin/activate
pip install ansible-core
```

## Deploy

```bash
cd ansible-monitoring
ansible-inventory --list   # sanity-check the inventory parses correctly
ansible all -m ping        # sanity-check SSH connectivity

ansible-playbook site.yml
```

This will:
1. Install and start `node_exporter` (bound to each node's private IP, port
   9100) on `controller`, `compute1`, `compute2`, `storage`.
2. Install and start `Prometheus` on `controller` (bound to `10.0.1.10:9090`),
   scraping every 20s, with `node` and `role` labels generated automatically
   from the inventory — no manual target list to maintain.

## Verify (automates the P0 checklist)

```bash
ansible-playbook verify.yml
```

- **1.2** — asserts `curl :9100/metrics` on every node returns real
  `node_cpu_seconds_total` data.
- **1.3** — queries Prometheus's `up{}` API and asserts every target is `1`,
  with `node`/`role` labels present, and that no target is missing.

The play fails loudly (non-zero exit code) if any check doesn't pass, so it's
safe to drop into a CI pipeline later.

## Access Prometheus UI

Prometheus binds to the private IP only (same posture as the OpenStack APIs).
Reuse the existing SSH tunnel pattern, adding the Prometheus port:

```bash
ssh -f -N \
  -L 9090:10.0.1.10:9090 \
  tunnel-ete@195.201.169.165
```

Then open `http://localhost:9090` → **Status → Targets**.

## Adding a new node later

Two separate steps now:

1. **Install node_exporter on the new VM** — add it to `inventory/hosts.ini`
   under the right group with its `node_role`, then re-run:
```bash
   ansible-playbook site.yml --limit <new-host>
```
   (This only touches node_exporter; it no longer touches Prometheus's scrape
   config.)

2. **Register it with Prometheus** — use the Node Registry API/UI
   (`/ui/nodes.html` or `POST /api/v1/nodes`) with its hostname, private IP,
   and role. Prometheus picks it up from `/etc/prometheus/file_sd/nodes.json`
   within `prometheus_file_sd_refresh_interval` (30s) — no restart needed.