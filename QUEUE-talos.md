# Talos: Kubernetes on VMs that survive a host failure

**The ask (2026-10-11):** move the cluster's nodes from LXC containers to Talos Linux VMs, gradually,
so that no single Proxmox host is a single point of failure: when one goes down, what ran there
comes back elsewhere by itself. Bring a fifth host into the Proxmox cluster for the capacity.

## Settled

- **Talos runs as VMs** (it can't run in LXC). The migration is in place, by Sidero's documented
  kubeadm-to-Talos path: Talos control planes join the existing cluster with its CA and etcd, one
  at a time; then Talos workers join; then the LXC nodes go. A Talos worker can't join while every
  control plane is still kubeadm (it needs a Talos control plane's trustd), so control planes come
  first. Checked against the cluster on 2026-10-12: Kubernetes v1.36.5, a single self-signed root CA
  (no intermediates, which Talos can't use), stacked etcd (three members, one per control plane);
  the three API servers share one service-account issuer and key pair; the API address is a DNS
  name kube-vip serves, so the handover changes no client.
- **Talos 1.14** (2026-08), which supports Kubernetes 1.33 to 1.37: the cluster's 1.36 now, and the
  1.37 upgrade (QUEUE-updates.md) afterwards through `talosctl upgrade-k8s`.
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
  hardware, and its SNMP agent takes 15 to 40 seconds to answer when idle. Replacing it is not
  part of this work (the owner, 2026-10-12: months or years away); meanwhile the backups on the
  TerraMaster (Garage, `tm-nfs`) must stay current and tested, so losing it means restoring, not
  losing data. A USB
  2.5 Gbit adapter isn't worth it on this model: the community driver (bb-qq/r8152) targets
  models from 2017 on (check its releases for an `avoton` build), and the CPU limits it anyway.
- **Size for the loss of the largest host:** with any one host down, the remaining workers must
  still hold every pod's memory request (21 GiB on 2026-10-11, growing as QUEUE-resource-limits.md
  adds the platform's). No host carries more than one control plane.

## The hosts (names stay out of git)

| | Threads | RAM | Today | Notes |
|---|---|---|---|---|
| H1 | 8 | 31 GB | control plane 1, worker 1 (4 cores), Pi-hole 2 | |
| H2 | 4 | 31 GB | worker 3 (control plane 3 retired 2026-10-13; its LXC kept off for now) | VT-x and VT-d on since 2026-10-12 |
| H3 | 4 | 15.5 GB | worker 2 | its NVMe is at 86% wear (QUEUE.md) |
| H4 | 4 | 31 GB | control plane 2, Pi-hole 1, Docker VM, domain controller VM | |
| H5 | 8 (i7-7700) | 63 GB | Talos control plane (VM 110) | in the cluster since 2026-10-12; its root is on the NX-512 (Phase 0) |

**Target layout** (adjust once H5 is in and measured):

| | Talos control plane (2 vCPU, 4 GiB) | Talos worker |
|---|---|---|
| H1 | yes | 4 vCPU, 12 GiB |
| H2 | no | 3 vCPU, 16 GiB |
| H3 | no (small, worn disk) | 2 vCPU, 6 GiB |
| H4 | yes | 2 vCPU, 8 GiB |
| H5 | yes | 6 vCPU, 20 GiB |

58 GiB of workers in all; losing the largest (H5) leaves 38 GiB, above the requests with room.
Control planes on H1, H4, H5 (the owner, 2026-10-13: the first Talos one was built on H5): losing
any one keeps etcd's quorum.

## Phase 1: preparation (no change to the running cluster)

- Shared storage for Proxmox HA already exists: `syn-nfs` (the Synology over NFS, shared, holds
  disk images). An HA group over all hosts.
- Move the Docker VM's and the domain controller VM's disks to it, and put both under Proxmox HA.
  Test: stop a host, the VM comes back elsewhere.
- The Talos image from Sidero's Image Factory: Talos 1.14.2 (2026-09-29), schematic
  in `talos/patches/common.yaml` (qemu-guest-agent; nfs-utils, whose rpc.statd every NFS
  volume here needs, since they all mount NFS v3; iscsi-tools and util-linux-tools for
  democratic-csi), the `nocloud` image so
  OpenTofu's cloud-init drive gives each VM its addresses. Machine configs from the cluster's
  PKI and a kubeadm bootstrap token (`talosctl gen secrets --from-kubernetes-pki`), generated
  on a control plane so the CA keys never leave it; `secrets.yaml` kept in Vaultwarden, never
  git. Generated with `cni: none`, and the pod and service subnets and the DNS domain read
  from `kubeadm-config`, and without `secretboxEncryptionSecret`: the
  kubeadm API servers have no encryption config, so a Talos one encrypting Secrets would write
  what they can't read. Encryption at rest comes after the last kubeadm control plane.
- Done 2026-10-12 for the guide: kube-proxy's DaemonSet recreated with `tier: node` in its
  selector; the service-account issuer `https://<API name>:6443` first on all three kubeadm
  API servers, the old in-cluster one kept second so existing tokens stay valid, both in
  `kubeadm-config` too. `k8s-upgrade.yml` skips kubeadm's kube-proxy phase and sets its image
  itself, since kubeadm's own DaemonSet has the old selector. Left: the API address handover
  (kube-vip on the kubeadm control planes today; Talos has its own shared VIP, which takes over
  once the last kubeadm control plane leaves; the API name stays, so no client changes).
- Calico's address detection: `kubernetes: NodeInternalIP` (2026-10-12), the kubelet's address,
  which the Talos configs pin to the main network (`KubeNodeConfig` `nodeIP.validSubnets`)
  and which equals what `interface: eth0` picked on every LXC node, so the switch moved no
  address. A Talos VM's interface isn't `eth0`, so the old pin would have left it NotReady.
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

- [x] Phase 0 (2026-10-12): H5 reinstalled with PVE 9.2.21 and joined (node 5, quorate); the same
      bridges by name (Work is `vmbr2` on VLAN 3, the one bridge not named after its VLAN), the
      `vms` pool on the whole Samsung 2 TB, `nas-block` on a LUN of its own (the plan said
      none; harmless, and it goes with the rest in Phase 4), Ansible's baseline and host
      playbooks run, Prometheus scraping it. Not as planned: Proxmox itself is on the NX-512
      and boots from the SanDisk's EFI partition; the owner chose to keep it so. Wear isn't the
      risk: 61% used after 57 TB, written at about 8 GB a day as a Proxmox root (2026-10-13),
      so about a decade of rated endurance left. What to watch is its available spare (78%) and
      its 9 media errors: a rising count of either means moving the root to the Samsung or a new
      disk. The 2 TB HDD still holds the old `HDD` volume group: wipe it once its two old VM
      disks (111, 117) are confirmed backed up
- [ ] Phase 1: Proxmox HA for the Docker VM and the domain controller. Done 2026-10-12: their
      disks on `syn-nfs` (raw, moved live), `tofu/proxmox/` matching for the Docker VM, both VMs
      in HA (started), the non-strict node-affinity rule `infra-vms` (H4 first, H5 next: the
      only other host with room for both), `shutdown_policy=migrate` and `crs ha=static` (HA
      places by configured memory, not by counting services). Left: the test, by cutting H4's
      power in a quiet window; both VMs should come back on H5 within about three minutes, then
      return to H4 once it's back
- [ ] Phase 1: Talos image (done) and machine configs (done: `talos/`, the secrets as
      `TALOS_SECRETS`); kube-proxy labels (done); the service-account issuer (done); the API
      address plan; Calico's address detection (done)
- [x] Phase 2 (2026-10-12/13): three Talos control planes, `[redacted]k8sc04a` (VM 110, H5, built by
      hand), `[redacted]k8sc06a` (113, H4) and `[redacted]k8sc05a` (112, H1), both through OpenTofu;
      the three kubeadm ones drained, `kubeadm reset` (which removed each etcd member) and
      deleted. etcd is the three Talos members. The API address moved from kube-vip to the
      Talos control planes' `Layer2VIPConfig`: kube-vip's manifest moved out on c01a, the
      config patched on with `talosctl patch mc --mode=no-reboot`, a 36-second gap, every node
      Ready throughout. Calico needed its FlexVolume driver off (`flexVolumePath: None`).
- [ ] After Phase 2: the kubeadm control planes' LXCs (101, 102, 103), stopped as fallbacks
      (`k8s_retired` in `guests.tf`), destroyed by hand after about a week, then taken out of
      `k8s_nodes` (a `removed` block can't drop one for_each instance; the plan after the
      destroy drops each from the state) and of Semaphore's Kubernetes inventory. Secrets
      encryption at rest: with no kubeadm API server left, the control planes' identity-only
      `KubeEtcdEncryptionConfig` (`talos/patches/controlplane-encryption*.yaml`) can become
      secretbox, then every Secret rewritten (`kubectl get secrets -A -o json | kubectl
      replace -f -`) so the old ones are encrypted too. VM 110's config is OpenTofu's since
      2026-10-13 (every built node applies with `no_reboot`). Until the
      workers are Talos, `k8s-upgrade.yml` can't run (no kubeadm control plane): no Kubernetes
      upgrade until Phase 3 is done, then `talosctl upgrade-k8s`
- [ ] Phase 3: the iSCSI storage driver; Talos workers on all five hosts; the databases and the
      single-instance stateful apps moved; the LXC workers removed
- [x] Corosync's second link on the 2.5 Gbit network (`vmbr1`, link 1, 2026-10-12), checked
      connected from every host, so a management network blip can't make HA reboot a healthy
      host. It was already the live-migration network
- [ ] Failover test: stop one host at a time; everything comes back on the others
- [ ] Phase 4: the kubeadm playbooks, LXC rules and `nas-block` removed

## Resuming

`kubectl get nodes -o wide` shows which nodes are Talos (OS image) and which are still LXC; the
first unticked box is next. Every step leaves a working cluster.

## Done when

Every node is a Talos VM, a host can be switched off with everything coming back on the others,
every box is ticked, and this file moves to `old-queues/`.
