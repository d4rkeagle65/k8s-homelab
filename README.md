# k8s-homelab

The configuration for a homelab: a Kubernetes cluster run by Flux v2 (GitOps), the Proxmox
guests and DNS records it sits on (OpenTofu), host maintenance (Ansible), and the services
that run beside the cluster on a Docker host. Nothing site-specific is kept here: real names,
addresses and secrets come from a password-manager vault or from the tools that use them.

## How it fits together

```
                                  Internet
                                     |  Cloudflare tunnel: the few public routes
                                     v
 +-------------------------- Kubernetes cluster ----------------------------------------+
 |  Ingress: Traefik (a MetalLB address) -> Authentik login -> apps                     |
 |           ExternalDNS writes each hostname to the Pi-holes                           |
 |  Control plane: 3 Talos VMs, etcd on each, one shared API address                    |
 |  Workers: Talos VMs (LXC containers being retired)                                   |
 |  Pods: Calico network; secrets from the vault via External Secrets; Flux syncs main  |
 |  Data: databases on node-local disk (2 instances each), app data on NFS,             |
 |        Prometheus/Alertmanager on NAS block storage                                  |
 |  Watching: Prometheus, Alertmanager, Grafana, Loki, Alloy                            |
 +--------------------------------------------------------------------------------------+
        |  each node: main network (default route) + management network (no gateway)
        v
 +-------------------- Proxmox cluster: 5 hosts, HA for the VMs outside Kubernetes ----+
 |  Kubernetes nodes (Talos VMs, LXC containers)   Pi-holes x2 (DNS)                   |
 |  Docker VM: Vaultwarden, Semaphore (Ansible, OpenTofu), Kea DHCP, Tailscale,        |
 |             Dockhand                                     domain controller VM       |
 +--------------------------------------------------------------------------------------+
   networks: main (pods, ingress, clients) | management (hosts, NAS, Docker VM, Proxmox API)
             | 2.5 Gbit (live migration, second cluster heartbeat link)
        |                                                 |
        v NFS (app data), iSCSI (block), HA VM disks      v backups
   Main NAS                                         Backup NAS: Garage (S3) for the databases'
                                                    WAL and nightly base backups; Proxmox backups
```

### Hardware and virtualisation

Turning it all off and on again, in order: `RUNBOOK-power.md`.

Five Proxmox hosts form one Proxmox cluster. A NAS provides the shared storage: NFS for
application data, iSCSI block storage, and the disks of the VMs that Proxmox HA restarts on
another host when theirs fails. A second NAS is the backup target, including an S3-compatible
object store (Garage, `docker/garage/`).

Beside the cluster, a Docker VM runs the services that must stay up when the cluster doesn't:
the password manager (Vaultwarden), the automation runner (Semaphore, for Ansible and
OpenTofu), DHCP (Kea), the VPN (Tailscale), container management (Dockhand), and backups of
its own databases. Two Pi-hole containers serve DNS.

### Kubernetes

- **Nodes:** three Talos Linux VMs form the control plane, sharing one API address, and the
  workers are Talos VMs too; `old-queues/QUEUE-talos.md` has how they moved from LXC
  containers.
- **Networking:** Calico for pods, MetalLB for LoadBalancer addresses, Traefik for ingress,
  and ExternalDNS writing the Ingress hostnames to the Pi-holes. The few public routes go out
  through a Cloudflare tunnel; everything else stays private.
- **Login and certificates:** Authentik for single sign-on, cert-manager for TLS.
- **Storage:** databases are CloudNativePG clusters of two instances on node-local disk
  (`local-db`), archiving to the backup NAS's S3 store; application data is on NFS from the
  NAS (`nfs-retain-rwo` and friends); Prometheus and Alertmanager, which write constantly,
  use block storage on the NAS (`nas-block`) to spare the hosts' SSDs.
- **Monitoring:** Prometheus, Alertmanager and Grafana (kube-prometheus-stack), with Loki for
  logs and Alloy collecting them, for the cluster and the machines around it.

### GitOps

Flux applies whatever is on `main`; a merge is a deploy. Most of `kubernetes/` is written by
`scripts/backup.py`, which captures the live cluster and generates the Flux files from it.
Secrets reach the cluster through External Secrets from a vault item, and Flux substitutes
`${VARIABLE}`s from those secrets and from `kubernetes/flux/meta/vars/`. Renovate proposes the
version updates.

### Infrastructure as code

- **OpenTofu** (`tofu/`): the Proxmox guests and the Talos machine configs, and the hand-made
  DNS records. Plans and applies run from Semaphore.
- **Ansible** (`ansible/`): the hosts' baseline, Proxmox and Pi-hole upgrades and settings,
  and maintenance of the LXC nodes, also run from Semaphore.
- **Docker stacks** (`docker/`): each deployed by Dockhand from its `compose.yaml`.

## Where to go next

| Path | What's there |
|---|---|
| `CLAUDE.md` | The rules for working in this repo, and the Flux Kustomization layout. |
| `kubernetes/` | The cluster's manifests (`prod/{system,services,apps}/`, `flux/`, `secrets/`, `shared/`, `test/`); its `CLAUDE.md` covers nodes, storage and ingress. |
| `scripts/` | The capture and generate tool; `scripts/README.md` explains it. |
| `talos/` | Talos patches and the machine-config generator; `talos/README.md`. |
| `tofu/` | OpenTofu modules; `tofu/README.md`. |
| `ansible/` | Playbooks and Semaphore setup; `ansible/README.md`. |
| `docker/` | Docker host stacks, each with its own README. |
| `initialization/` | Notes and a script for building the original LXC nodes. |
| `tests/` | The test suite the pre-commit hook runs; `tests/README.md`. |
| `QUEUE.md`, `QUEUE-<task>.md` | Outstanding work, and one file per long job; finished ones are in `old-queues/`. |
