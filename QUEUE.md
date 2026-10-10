# Homelab work queue

Outstanding work on the cluster and on this repo, roughly in the order worth doing it.
`CLAUDE.md` holds the rules for keeping it.

## Contents

- [Waiting on the owner](#waiting-on-the-owner)
- [Next](#next)
- [Cleanup](#cleanup)
- [Later](#later)
- [Working queue files](#working-queue-files) - `QUEUE-terraform.md`, `QUEUE-monitoring.md`, `QUEUE-dashboards.md`.

## Waiting on the owner

Decisions that unblock the work below; each gives a recommendation. Numbers stay as
assigned (items below refer to them); 1 to 4, 6, 8 and 9 are done.

5. **Kea's standby partner:** Kea's reservations live in PostgreSQL on the Docker VM, so a
   partner elsewhere can't read them while that VM is down, which is when it's needed. Options:
   (a) the partner keeps a copy of the reservations in its own config (two places to change);
   (b) the reservations move to a database that survives the Docker VM (a CloudNativePG
   cluster, reached through a LoadBalancer address); (c) leave HA for leases only. Recommended:
   (b), with the partner on the second Pi-hole's host.
7. **Docker's live-restore on the Docker VM, then Docker upgrades from Semaphore.** Agreed
   2026-10-06; to build in `docker-vm-update.yml`, both parts off until set:
   - `docker_live_restore: true` merges `"live-restore": true` into `/etc/docker/daemon.json`
     (keeping whatever else is there), runs `systemctl reload docker` (live-restore is a
     reloadable setting, so nothing restarts), and reads `docker info` back
     (`LiveRestoreEnabled`).
   - `upgrade_docker: true` unholds, upgrades and re-holds Docker's packages, only when
     `docker info` says live-restore is on and the new `docker-ce` is the same major version
     (Docker supports live-restore across minor and patch upgrades, not major ones; a major
     upgrade is reported for a run by hand). Before and after, it reads the running containers
     and stops if any that ran before is gone.
10. **The two test folders** (item below): move `scripts/tests` under `tests/` with one runner,
    or keep both and say why in each folder's CLAUDE.md. Recommended: keep both; the generator's
    suite needs its own fixtures and runs offline, the gate is repo-wide.

## Next

- **The garage stack's config is an inline compose config** (`docker/garage/compose.yaml`,
  `configs: content`), the same mechanism that left `vaultwarden-backup` without its rclone
  file after a recreation on 2026-10-10 (that container restarted about 600 times and made
  no backup until it was moved to environment variables). Garage still has its file, through
  Hawser on the NAS; if a recreation ever drops it, Garage starts without its config. Move it
  to a form that doesn't depend on the copy (environment variables, or a file written at
  start), and have Prometheus alert on a container restarting in a loop (cAdvisor's
  `container_start_time_seconds` changing).

- **The second Proxmox host's NVMe drive (512 GB, the one carrying its guests) is at 86% of its
  rated wear** (`nvme_percentage_used_ratio`, 2026-10-10: 62 TB written over 4.1 years, about
  42 GB a day on average): at that rate it reaches 100% in about eight months. Replace it, a
  ZFS disk swap. The first host's 256 GB boot drive is at 66% (about 1.4 years). Every host's
  drives write 40 to 70 GB a day on lifetime average, far more than Prometheus did: once a few
  days of `node_disk_written_bytes_total` and `pve_disk_written_bytes` exist, find the writers
  (etcd, the workers' databases, images and logs, Proxmox's own files) and cut what can be.
  Alerts on wear and spare capacity are phase 5 of `QUEUE-monitoring.md`.

- **NAS block storage, live (2026-10-10):** `nas-block` (`ansible/tasks/proxmox-nas-block.yml`)
  is active on all four hosts, 30 GiB each, one path per host, and each worker has a 28 GiB
  `mp2` on it at `/opt/nas-block` (added live with `pct set`; declared in `tofu/proxmox/`,
  whose plan should show no change for them). Next: the `nas-block` StorageClass for
  Prometheus and Alertmanager (`QUEUE-monitoring.md`).

- **The nodes' management access, built and off:** `ansible/tasks/k8s-node-mgmt.yml` (run by
  `k8s-node-update.yml`). Set `mgmt_gateway` in the "Kubernetes" variable group, run the
  update, and SSH from the PC to a node's management address; then `mgmt_ssh_only: true` and
  run again. Until then the management addresses answer only on the management network.

- **Kubernetes 1.37, when its patches settle:** add bundle 1.37 to `ansible/k8s-bundles.yml`
  (copy 1.36; versions and each add-on's range from its support page). Calico 3.33 is the one
  that covers 1.37 (3.33.0 tested on 1.35-1.37); check the others' ranges then. Bump the
  claude-code image's `KUBECTL_VERSION` once the cluster is on it.

- **Proxmox backups of the workers' mount points:** each worker's `mp0` (container images)
  carries `backup=1` in the Proxmox job, and its database dataset `mp1` doesn't. With every
  CloudNativePG database backed up to Garage and restore-tested (2026-10-09), `mp1` needn't
  be, and `mp0` could drop it.

## Cleanup

- **`test/media/jackett`** stays as a test-only app for now; not listed in
  `test/kustomization.yaml`.

## Later

- **Faster recovery from a node loss:** every database now fails over in under a minute, but
  the app pods on a lost node wait out the default 5-minute
  `node.kubernetes.io/unreachable`/`not-ready` tolerations before moving. Shortening those to
  about 30 seconds on the apps that matter (immich first) closes most of the gap.
- **Proxmox host cleanup.**
  - Once kernel 7.0 has run cleanly for a couple of weeks (from about 2026-10-16), set
    `remove_old_kernels: true` on the Proxmox inventory and run the upgrade template: with the
    default `keep_kernels: 2` it removes `6.14.11-4` and keeps `6.14.11-9` as the fallback.
    Every run already lists what it would remove.
  - Don't run `zpool upgrade` unless a new ZFS feature is needed.

- **The Pi-holes' sshd waits for the network by a hand-made drop-in**
  (`ssh.service.d/20-after-network-online.conf`, 2026-10-10): their sshd listens on the
  management address only and failed to bind at a boot without it. `baseline.yml` could own
  that drop-in on any host whose sshd sets `ListenAddress`, so a rebuilt Pi-hole gets it.
- **Kea phase 3** (decision 5): a second Kea on another host as a hot-standby partner
  (the firewall's relay can point at both), and lease names into DNS.
- **DMZ clients don't know about the second Pi-hole**: Kea doesn't serve the DMZ, so its
  static clients still list only `172.16.228.3` and lose DNS while Pi-hole 1's host
  reboots. Add `172.16.228.4` on each by hand.
- **Switch on VT-x in the third Proxmox host's BIOS** ("Intel Virtualization Technology"): its CPU
  supports it but the host reports no `vmx` flag, so it can't run any VM, and it's the Docker
  VM's evacuation target (the most free memory). The 2026-10-08 upgrade's migration there
  failed; `ansible/tasks/evacuate-check.yml` now stops a run before trying. It can't be set
  from Linux: the kernel's `hp-bioscfg` reads the settings but this 2016 firmware refuses
  writes (`hp_bioscfg: Returned error 0x4, "Invalid command type"`), so it's F10 at boot,
  Advanced → System Options. Until then the Proxmox inventory's `evacuate` points at another
  host; with the ARC caps (`proxmox-host.yml`) that one has room without a temporary cap.
- **The Docker VM's host still carries DNS, DHCP, the VPN and Vaultwarden together**: a Kea
  HA partner on another host (decision 5) is what's left once both Pi-holes are in use.
- **Dockhand's own data** (`/opt/dockhand`, SQLite: its environments, Git stacks and their
  variables, secrets included) has no backup of its own beyond the Proxmox backup of the Docker
  VM's disks. The databases and Vaultwarden there are backed up to Garage nightly
  (`docker/databasus/`, `vaultwarden-backup` in `docker/vaultwarden/`).
- **Ansible for the hosts outside the cluster**, so their hand-made setup
  can be rebuilt from git instead of from notes:
  - **Docker VM:** netplan (its three network legs, routing tables and rule
    priorities), the nft scripts and their systemd units (management return
    path, DMZ marks, the `DOCKER-USER` rules for Tailscale), sysctls, Docker,
    Dockhand's own compose file and the certificate-sync cron.
  - **Proxmox hosts:** `ansible/proxmox-host.yml` holds the ZFS ARC cap, the ACME
    certificates and the NAS block storage (all applied), and `baseline.yml` the common
    packages. Still to add there: repositories, kernel cleanup, and the host settings the k8s
    LXCs depend on: kernel modules, sysctls, and the parts of
    each LXC's `/etc/pve/lxc/<id>.conf` an API token can't set (the raw
    `lxc.*` lines and the `mount=nfs` feature; OpenTofu ignores them,
    `QUEUE-terraform.md`).
  - **A k8s node join:** prepare a fresh LXC (repositories, CRI-O, kubelet
    and kubeadm at the current bundle's versions, the ssh.socket fix) and
    `kubeadm join` it as a worker or control plane, from the `k8s-node-*`
    tasks. Without it a node OpenTofu creates is an empty container.
  - **Pi-hole:** `ansible/pihole-config.yml` sets what nebula-sync can't copy
    (`misc.dnsmasq_lines`) on both, from Semaphore. Everything else (local
    records such as `vaultwarden-direct`, CNAMEs, lists, `bogusPriv`) lives on
    Pi-hole 1 and is copied to Pi-hole 2 by nebula-sync, so losing both would
    lose it: a scheduled Teleporter export to Garage would cover that.
  - **Named logins instead of root:** the owner's account with sudo is on every machine
    (`baseline.yml`, run 2026-10-10). Once it's proven everywhere: remove the owner's
    key from root's `authorized_keys` (on the Proxmox hosts the shared
    `/etc/pve/priv/authorized_keys`), and set `sshd` to `PasswordAuthentication no` and
    `PermitRootLogin prohibit-password` (never `no`: the Proxmox hosts and Semaphore log in
    as root by key), from a playbook that first checks the named login works.
  - **Scheduled check-mode runs in Semaphore** (weekly, failure alerts on),
    reporting pending updates and drift without changing anything.

  The inventory and every address stay out of this public repo (a
  gitignored inventory, or values from Vaultwarden).
- **Terraform (or OpenTofu) for what's created through APIs** (decision 9):
  - **Proxmox:** the VMs and LXCs (k8s nodes, the Docker VM, Pi-hole), with
    their disks and network interfaces.
  - **Cloudflare:** DNS records and the tunnel's public hostnames.
  - **The *arr settings:** download clients, root folders, auth and
    Prowlarr's app links, through the devopsarr providers. This is the
    alternative to a small in-cluster Job calling the apps' APIs, which
    needs no state file.
  - **Dockhand:** its Git credentials, the Git stacks it deploys from
    `docker/<stack>/` and their variables, plus its own settings and login,
    through the community `kalebharrison/dockhand` provider (unofficial,
    with known gaps, so a Dockhand update can break it). Dockhand can't set
    itself up: Ansible installs it on the VM, then Terraform configures it.

  In progress, Proxmox first: [QUEUE-terraform.md](QUEUE-terraform.md) has the
  decisions (state in Semaphore's PostgreSQL, plans and applies run from
  Semaphore, existing resources imported first) and the checklist.

- **Review moving the cluster to Talos Linux** (low priority; a review first, not a decision).
  Talos is a minimal, API-managed OS made only to run Kubernetes: no SSH, no package manager,
  configured from one machine config per node and upgraded with `talosctl upgrade` (OS) and
  `talosctl upgrade-k8s` (Kubernetes) as atomic image swaps that roll back on failure. What it
  would change here:
  - **It replaces most of the upgrade machinery (`ansible/k8s-upgrade.yml`, `old-queues/QUEUE-k8s-upgrade.md`):** kubeadm, the apt sources,
    CRI-O (Talos uses containerd), the node update playbook and most of the version bundles.
    The add-on ranges and the database drain steps stay useful.
  - **It can't run in LXC.** Talos is its own OS, so every node becomes a VM: more memory per
    node than a container, and one host has only 15 GB. Count what the six nodes need as VMs
    before anything else.
  - **VMs get block devices**, which these LXC nodes can't: Longhorn or another replicated
    storage becomes possible, so databases on `local-db` could stop being pinned to one worker.
  - **Migration is a rebuild:** a new cluster beside the old, Flux bootstrapped onto it from this
    repo, workloads and data moved (CloudNativePG databases by replica or backup/restore, PVC
    data copied), then the old nodes removed. It fits the Terraform item (VMs and machine
    configs from code).
  - **Things to check:** kube-vip versus Talos's built-in shared VIP; Calico's support on Talos;
    the NFS mounts; how `scripts/` captures a cluster with no node access; Semaphore's role once
    the nodes have no SSH (talosctl from Semaphore, or Omni).
- **Consolidate the two test folders** (low priority; decision 10). `tests/` is the repo-wide gate
  (doc-style, harness, hygiene, script-health suites, run by `tests/run-all-tests.py` and the
  pre-commit hook); `scripts/tests/` is the generator's pytest suite, reached only through
  `tests/test-scripts-pytest.py`. Either move the generator's tests under `tests/` (say
  `tests/scripts/`) with one runner, or leave the layout and make the split obvious in
  `tests/CLAUDE.md` and `scripts/CLAUDE.md`. Left as is, a new test can land in the wrong
  folder and skip the checks it was meant for.

## Working queue files

- [QUEUE-terraform.md](QUEUE-terraform.md): OpenTofu, Proxmox guests first.
- [QUEUE-monitoring.md](QUEUE-monitoring.md): Prometheus, Loki and Grafana, in eight phases.
- [QUEUE-dashboards.md](QUEUE-dashboards.md): Grafana's dashboards and Homepage.

A long job gets a `QUEUE-<task>.md` at the repo root; `CLAUDE.md` says when. A
finished one moves to `old-queues/`, whose README indexes what each settled and which checks
came back negative.
