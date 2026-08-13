# RIF SAS — OpenStack Private Cloud Knowledge Base

This directory is the source-of-truth knowledge base for the RIF SAS OpenStack private
cloud (Hetzner-hosted, deployed with Kolla-Ansible). It is the corpus ingested by Cortex's
RAG pipeline (`services/api/app/services/knowledge/`) into the `cortex-knowledge` collection
on Qdrant Cloud, so that operators and the Cortex assistant can retrieve accurate, current
answers about this infrastructure instead of relying on model memory.

| Field | Value |
|---|---|
| Organization | RIF SAS — Rassemblement des Ingénieurs Francophones |
| Version | 2.0 — July 2026 |
| Platform | OpenStack 2024.1 (Caracal) via Kolla-Ansible |
| Host | Hetzner Online GmbH — Dedicated servers |
| Classification | Internal document — Restricted use |

## Why this cloud exists

RIF SAS (Rassemblement des Ingénieurs Francophones), an engineering company based in Paris
and Champs-sur-Marne, built its own private Cloud to support its technological growth and
train its engineers: technological independence from public clouds, continuous hands-on
training for engineers/interns, predictable fixed cost, full control over data/security/access,
and a safe R&D sandbox outside of production.

**Objectives:** a complete IaaS platform based on OpenStack · on-demand VM creation
(Horizon web UI or CLI) · a virtual network isolated per project with dynamic public IPs ·
persistent block storage (Cinder) · full automation (Terraform + Ansible) · environments
fully isolated per team/project/intern.

## Technology stack

| Technology | Version | Role |
|---|---|---|
| OpenStack | 2024.1 (Caracal) | Main IaaS Cloud platform |
| Kolla-Ansible | 17.x | OpenStack deployment via Docker |
| Docker | 26.x | Containerization of OpenStack services |
| Ubuntu Server | 22.04 LTS | OS for physical servers and VMs |
| KVM / QEMU | Native kernel | Virtualization hypervisor |
| Open vSwitch | 3.x | Virtual network switch (SDN) |
| Terraform | 1.7.5 | Infrastructure as Code |
| Ansible | 2.17.x | Configuration automation |
| MariaDB | 10.x | Services database |
| RabbitMQ | 3.x | Inter-service message bus |

## How this knowledge base is organized

| File | Covers |
|---|---|
| `topology.md` | Hosting provider choice, physical servers, node roles, inter-node links |
| `network.md` | SDN model, OVS bridges, internal/external networks, public subnets |
| `service-catalog.md` | Full OpenStack service map (ports, roles) and container layout |
| `service-detail/nova.md` | Nova (Compute) components and behavior |
| `service-detail/neutron.md` | Neutron (Networking) components and behavior |
| `service-detail/glance.md` | Glance (Image) catalog and configuration |
| `service-detail/keystone.md` | Keystone (Identity) concepts and RBAC |
| `service-detail/cinder.md` | Cinder (Block Storage) backend and transport |
| `resource-mgmt.md` | Flavors, project quotas, cloud-init, security groups |
| `security-access.md` | Authentication methods, multi-tenant isolation, SSH tunneling |
| `admin-runbook.md` | Installation procedure, day-2 commands, monitoring thresholds |
| `flow-processes.md` | Step-by-step operational flows (VM creation, deployment) |
| `glossary.md` | Definitions for every OpenStack/infra term used across this KB |

## Key points

Multi-node architecture (1 controller + 2 compute + 1 storage, Control/Data Plane
separation) · Kolla-Ansible (standardized services in Docker containers) · SDN networking
(Open vSwitch, VXLAN tunnels, routers in namespaces) · LVM storage on a dedicated node,
exposed via iSCSI · security through Keystone multi-tenant isolation, security groups, a
restricted SSH tunnel · automation via Terraform (provisioning) + Ansible (configuration).

## Roadmap / known gaps

High availability (2nd controller, auto failover) · monitoring (Prometheus + Grafana) ·
distributed storage (Ceph as Cinder/Glance backend) · CI/CD (GitLab CI pipeline) ·
centralized SSH bastion.

*RIF SAS — Internal Technical Document — July 2026*
