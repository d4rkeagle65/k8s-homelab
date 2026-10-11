# Talos: Kubernetes on VMs that survive a host failure

**The ask (2026-10-11):** move the cluster's nodes from LXC containers to Talos Linux VMs, gradually,
so that no single Proxmox host is a single point of failure: when one goes down, what ran there
comes back elsewhere by itself. Bring a fifth host into the Proxmox cluster for the capacity.

## Settled

- **Talos runs as VMs** (it can't run in LXC). The migration is in place, by Sidero's documented
  kubeadm-to-Talos path: Talos control planes join the existing cluster with its CA and etcd, one
  at a time; then Talos workers join; then the LXC nodes go. A Talos worker can't join while every
  control plane is still kubeadm (it needs a Talos control plane's trustd), so control planes come
  first. Checked against the cluster on 2026-10-11: Kubernetes v1.36.5 (Talos 1.13 supports 1.31-1.36),
  a single self-signed root CA (no intermediates, which Talos can't use), stacked etcd.
- **Two layers of failover, each doing what it's good at:**
  - *Kubernetes* for everything in the cluster. Nodes are cattle on local disks: when a host
    dies, its Talos VMs die with it and Kubernetes restarts their pods on the other hosts' nodes.
    That only works for pods whose storage isn't tied to the dead node, hence the storage rules.
  - *Proxmox HA* for the guests outside the cluster: the Docker VM (Vaultwarden, Semaphore, Kea,
    Tailscale, Dockhand) and the domain controller VM. Proxmox HA restarts a VM on another host
    only if its disks are on shared storage: the NAS (NFS, or an iSCSI LUN shared as LVM).
    Longhorn can't do this: it stores Kubernetes volumes, not Proxmox VM disks.
  - Talos VMs are not put in Proxmox HA: their disks stay local (etcd and the databases need
    local latency), and Kubernetes already covers a lost node.
- **Storage, so nothing in the cluster is tied to one node:**
  - CloudNativePG databases stay on node-local disk (`local-db`): two instances on different
    hosts, and CloudNativePG fails over by itself. Already true.
  - App data stays on the NAS over NFS (any node mounts it). Already true.
  - The few single-instance stateful things that need a block device (Grimmory's MariaDB,
    Prometheus, Loki, CouchDB) move to iSCSI volumes created per claim on the Synology by a CSI
    driver (democratic-csi's Synology driver), so the volume follows its pod to any node. This
    replaces `nas-block` (one iSCSI LUN per Proxmox host, mounted into an LXC node), which ties
    Prometheus to a node today.
  - Longhorn is not used: it replicates every write to two or three local SSDs, and SSD wear is
    already a problem here (QUEUE.md).
- **The NAS is the one shared dependency left:** every NFS volume, the iSCSI volumes, and the VM
  disks Proxmox HA restarts. It's a Synology DS415+ (2014: Atom C2538, 2 GB RAM, two 1 Gbit
  ports, both in use, no PCIe slot). Its board was repaired in January 2026 after its CPU failed
  (the Atom C2000 fault, Intel's AVR54), so that fault is fixed, but the rest of it is 2014
  hardware, and its SNMP agent takes 15 to 40 seconds to answer when idle. Replacing it is part of
  this work, not optional (see the checklist); until then, the backups on the TerraMaster (Garage,
  `tm-nfs`) must stay current and tested, so losing it means restoring, not losing data. A USB
  2.5 Gbit adapter isn't worth it on this model: the community driver (bb-qq/r8152) targets
  models from 2017 on (check its releases for an `avoton` build), and the CPU limits it anyway.
- **Size for the loss of the largest host:** with any one host down, the remaining workers must
  still hold every pod's memory request (21 GiB on 2026-10-11, growing as QUEUE-resource-limits.md
  adds the platform's). No host carries more than one control plane.

## The hosts (names stay out of git)

| | Threads | RAM | Today | Notes |
|---|---|---|---|---|
| H1 | 8 | 31 GB | control plane 1, worker 1, domain controller VM, Pi-hole 2 | |
| H2 | 4 | 31 GB | control plane 3, worker 3 | |
| H3 | 4 | 15.5 GB | control plane 2, worker 2 | its NVMe is at 86% wear (QUEUE.md) |
| H4 | 4 | 31 GB | Pi-hole 1, Docker VM | no Kubernetes node yet |
| H5 | 8 (i7-7700) | 64 GB | Proxmox VE 7.4-17, standalone, old guests, nothing running | to join the cluster |

**Target layout** (adjust once H5 is in and measured):

| | Talos control plane (2 vCPU, 4 GiB) | Talos worker |
|---|---|---|
| H1 | yes | 4 vCPU, 12 GiB |
| H2 | yes | 3 vCPU, 12 GiB |
| H3 | no (small, worn disk) | 2 vCPU, 6 GiB |
| H4 | yes | 2 vCPU, 8 GiB |
| H5 | no | 6 vCPU, 20 GiB |

58 GiB of workers in all; losing the largest (H5) leaves 38 GiB, above the requests with room.
Control planes on H1, H2, H4: losing any one keeps etcd's quorum.

## Phase 0: the fifth host joins the Proxmox cluster

Nothing runs on H5, and every guest on it has a backup. A node can only join a Proxmox cluster
with no guests and no cluster of its own, and the cluster runs PVE 9 (Debian trixie).

- **Decided: reinstall** PVE 9 from the ISO (the owner, 2026-10-11), rather than two in-place major
  upgrades (7 to 8 to 9) on a box full of old guests and packages. First make sure every backup
  of its old guests is off the box (a reinstall wipes its disks): the owner keeps them on the
  TerraMaster, outside `tm-nfs`, so they're checked there, along with `local512`'s 121 GB.
- **Disks** (SMART, 2026-10-11): root (64 GB, the installer's disk size limit) and the `vms` pool
  (the rest) both on the Samsung 970 EVO Plus 2 TB (1% used, no errors). Not the NX-512 NVMe
  (61% used, 9 media integrity errors) nor the SanDisk SSD Plus 120 GB (4 years on, few rated
  writes; a cluster member's root writes a lot): spares. The 2 TB HDD stays out.
- **Network:** its switch port moves to the other hosts' profile (management untagged; VLANs 3,
  4, 6, 7, 8, 9 tagged) during the reinstall: today management reaches it tagged (VLAN 5) and
  production untagged. Addresses by the other hosts' pattern: the same last number on the
  management network (`vmbr0`) and the 2.5 Gbit network (`vmbr1`, its 2.5 Gbit port).
- Match the other hosts (read from the Proxmox API, 2026-10-11): every host has the same bridges,
  by name, and guests refer to them, so a guest only moves to a host that has them:
  - `vmbr0`: the management network, untagged, with the host's address and the default route
    (the installer creates it on the NIC chosen; give it the management address and gateway);
  - `vmbr1`: on the second NIC, with an address and no gateway (the NAS's storage side);
  - `vmbr2`, `vmbr4`, `vmbr6`, `vmbr7` (the main network), `vmbr8`, `vmbr9`: one per VLAN 3, 4, 6, 7,
    8, 9 on the first NIC (`<nic>.<vlan>`), no addresses.
  The other hosts' NICs are named `eth5`/`eth6`; only the bridge names have to match.
- Storage: a ZFS pool named `vms` for local guest disks (the other hosts' `vms` storage lists its
  hosts, so H5 is added to it); `syn-nfs`, `tm-nfs` and `nas-iscsi` are shared and reach every host
  without changes. No `nas-block` LUN: Talos replaces it.
- Before joining: its management address and name in Pi-hole, the no-subscription repository,
  root's SSH key for Semaphore, and the Ansible inventories (`proxmox` group, All Debian Hosts) so
  `baseline.yml` and `proxmox-host.yml` give it the baseline, the ZFS ARC cap and its certificate.
  Then `pvecm add <an existing host>` from H5, over the management network.
- Afterwards: 5 votes (quorum 3); `proxmox-upgrade.yml` covers it.

## Phase 1: preparation (no change to the running cluster)

- Shared storage for Proxmox HA already exists: `syn-nfs` (the Synology over NFS, shared, holds
  disk images). An HA group over all hosts.
- Move the Docker VM's and the domain controller VM's disks to it, and put both under Proxmox HA.
  Test: stop a host, the VM comes back elsewhere.
- The Talos image from Sidero's Image Factory: the QEMU guest agent and iSCSI tools extensions,
  pinned to a Talos 1.13 release matching Kubernetes v1.36. Talos machine configs generated from
  the cluster's CA and a kubeadm bootstrap token (`kubeadm token create`), kept out of git apart
  from what's public; secrets in Vaultwarden.
- Fix what the migration guide requires: kube-proxy's pods labelled `tier: node` as well as
  `k8s-app: kube-proxy` (only `k8s-app` today); the service-account issuer the same on old and new
  control planes; decide the API address handover (kube-vip on the kubeadm control planes today;
  Talos has its own shared VIP, which takes over once the last kubeadm control plane leaves).
- Calico on Talos VMs: its address detection is pinned to `eth0` (CLAUDE.md, kubernetes/); give
  the VMs predictable `eth0`/`eth1` names (`net.ifnames=0`) or switch detection to the main
  subnet (`cidrs:`) for all nodes first.
- The VMs through OpenTofu (`tofu/proxmox/`), like the other guests.

## Phase 2: control planes

One at a time, each fully healthy before the next (etcd has 3 members, plus the new one while
it joins): create the Talos control plane VM, let it join (etcd member, API server), check etcd
health and every node Ready, then drain and remove one kubeadm control plane (`kubeadm reset`,
remove its etcd member, delete the node, delete the LXC). Last: the API address moves from
kube-vip to Talos's VIP.

## Phase 3: workers

One host at a time: create the Talos worker, label it like the old one (`local-db`, NFS), move
the databases (each CloudNativePG cluster gets an instance on the Talos worker, switches over to
it, and drops the old one; no restore), let the stateless pods move (drain), then remove the LXC
worker. The storage driver for iSCSI volumes comes in with the first Talos worker, and Grimmory's
MariaDB, Prometheus, Loki and CouchDB move to it.

## Phase 4: clean-up

The Ansible playbooks for the kubeadm nodes (`k8s-node-update.yml`, `k8s-node-reboot.yml`,
`k8s-upgrade.yml`, `k8s-bundles.yml`, the node tasks) are replaced by `talosctl upgrade` and
`talosctl upgrade-k8s`; the LXC-specific rules in `kubernetes/CLAUDE.md` go; `nas-block` goes.

## Checklist

- [ ] Phase 0: H5's old backups confirmed off the box; PVE 9 installed; bridges, `vms` pool and
      repository matching; in the Ansible inventories with the baseline run; joined to the Proxmox
      cluster
- [ ] Phase 1: shared storage for HA; the Docker VM and the domain controller under Proxmox HA,
      tested by stopping a host
- [ ] Phase 1: Talos image and machine configs; kube-proxy labels; the service-account issuer;
      the API address plan; Calico's address detection
- [ ] Phase 2: three Talos control planes (H1, H2, H4), the kubeadm ones removed, Talos's VIP
- [ ] Phase 3: the iSCSI storage driver; Talos workers on all five hosts; the databases and the
      single-instance stateful apps moved; the LXC workers removed
- [ ] The 2.5 Gbit network (isolated, every host's second port, `vmbr1`): already
      the live-migration network; add it as corosync's second link before enabling Proxmox HA, so
      a management network blip can't make HA reboot a healthy host
- [ ] A replacement for the DS415+, with 2.5 or 10 Gbit on the storage network (built in or a
      PCIe card), so the NAS isn't a 2014 box on a shared 1 Gbit trunk; migrate its shares and
      LUNs; the TerraMaster backups checked by a restore first
- [ ] Failover test: stop one host at a time; everything comes back on the others
- [ ] Phase 4: the kubeadm playbooks, LXC rules and `nas-block` removed

## Resuming

`kubectl get nodes -o wide` shows which nodes are Talos (OS image) and which are still LXC; the
first unticked box is next. Every step leaves a working cluster.

## Done when

Every node is a Talos VM, a host can be switched off with everything coming back on the others,
every box is ticked, and this file moves to `old-queues/`.
