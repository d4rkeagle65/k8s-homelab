# Homelab work queue

Outstanding work on the cluster and on this repo, roughly in the order worth doing it.
`CLAUDE.md` holds the rules for keeping it.

## Contents

- [Waiting on the owner](#waiting-on-the-owner)
- [Next](#next)
- [Cleanup](#cleanup)
- [Later](#later)
- [Working queue files](#working-queue-files) - `QUEUE-semaphore.md`, `QUEUE-terraform.md`.

## Waiting on the owner

Decisions that unblock the work below; each gives a recommendation. Numbers stay as
assigned (items below refer to them); 1 to 4 and 9 are done.

5. **Kea's standby partner:** Kea's reservations live in PostgreSQL on the Docker VM, so a
   partner elsewhere can't read them while that VM is down, which is when it's needed. Options:
   (a) the partner keeps a copy of the reservations in its own config (two places to change);
   (b) the reservations move to a database that survives the Docker VM (a CloudNativePG
   cluster, reached through a LoadBalancer address); (c) leave HA for leases only. Recommended:
   (b), with the partner on the second Pi-hole's host.
6. **SSH to the nodes on the management network only:** the nodes' `eth1` has no gateway, so
   today only machines on that network can reach their management addresses (the PC's attempts
   time out or reset). Proposed: give each node a routing table for replies from its `eth1`
   address via the management network's gateway (as the Docker VM has), then `sshd` listens on
   that address only, both from `k8s-node-update.yml`, off until set. Needed: the management
   network's gateway address, and a yes.
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
8. **Keep immich and manictime up through a node loss** (item below): `instances: 2` doubles
   their database disk on another worker. With in-place updates (1.) the remaining downtime is
   a node's drain. Recommended: yes for immich (photos), optional for manictime.
10. **The two test folders** (item below): move `scripts/tests` under `tests/` with one runner,
    or keep both and say why in each folder's CLAUDE.md. Recommended: keep both; the generator's
    suite needs its own fixtures and runs offline, the gate is repo-wide.

## Next

- **Semaphore for Ansible, starting with keeping the Proxmox hosts up to date.** In progress;
  see [QUEUE-semaphore.md](QUEUE-semaphore.md).
- **Kubernetes 1.37, when its patches settle:** add bundle 1.37 to `ansible/k8s-bundles.yml`
  (copy 1.36; versions and each add-on's range from its support page). Calico 3.33 is the one
  that covers 1.37 (3.33.0 tested on 1.35-1.37); check the others' ranges then. Bump the
  claude-code image's `KUBECTL_VERSION` once the cluster is on it.

- **Database backups: restore-test the other ten.** Since 2026-10-06 all eleven CloudNativePG
  clusters archive WAL continuously to Garage on the NAS (`docker/garage/`, the Barman Cloud
  plugin) and take a nightly base backup (02:00 to 02:45, mealie at 02:30), kept 30 days.
  Only mealie's has been restored (into a scratch cluster, matching the live database table for
  table). Until each of the others has been, its backup is unproven: restore each the same way
  (a one-instance Cluster on `nfs-eph-rwo` bootstrapped from its `ObjectStore`, compare row
  counts, delete it).
  - **A Cluster that names an `ObjectStore` before it exists stays stuck:** the plugin's
    pre-reconcile hook stops the reconcile ("Pre-reconcile hook stopped the reconciliation
    loop") and the operator doesn't retry once the store appears. Four of the ten did that; an
    annotation on the Cluster made the operator reconcile again. Create the store first.
  - **`ContinuousArchiving: True` before a pod has the plugin's sidecar proves nothing:**
    nothing has tried to archive yet. Check `pg_stat_archiver` on the primary instead.
  - Separately, each worker's `mp0` (container images) carries `backup=1` in the Proxmox job,
    and its database dataset `mp1` doesn't; with these backups `mp1` needn't, and `mp0` could
    drop it.

- **n8n** (workflow automation; branch `n8n`, after `n8n-vars`): app-template with the
  `n8nio/runners` sidecar (external task runners, which n8n's docs require wherever real
  credentials are stored), CloudNativePG with backups from day one, LAN only behind
  `private-networks`, n8n's own login (its OIDC is Enterprise). Before merging: the
  `N8N_ENCRYPTION_KEY` and `N8N_RUNNERS_AUTH_TOKEN` fields in Vaultwarden, then `n8n-vars`.
  After: create the owner account at first visit, and restore-test its database once the
  first nightly backup has run.

## Cleanup

- **`test/media/jackett`** stays as a test-only app for now; not listed in
  `test/kustomization.yaml`.

## Later

- **Keep immich and manictime up through a node loss** (decision 8). Today
  their single database copy sits on one worker's disk.
  - Set `instances: 2` on `immich-postgres` and `manictime-pg`, for automatic
    failover in under a minute.
  - Shorten the app pods' `node.kubernetes.io/unreachable`/`not-ready`
    tolerations from the default 5 minutes to about 30 seconds.
  - Cost: double the database disk on another worker.
- **Proxmox host cleanup.**
  - Once kernel 7.0 has run cleanly for a couple of weeks (from about 2026-10-16), set
    `remove_old_kernels: true` on the Proxmox inventory and run the upgrade template: with the
    default `keep_kernels: 2` it removes `6.14.11-4` and keeps `6.14.11-9` as the fallback.
    Every run already lists what it would remove.
  - Don't run `zpool upgrade` unless a new ZFS feature is needed.

- **SSH to the k8s nodes only from the management network** (decision 6). Each node
  now has a management-network interface (`eth1`), so sshd can listen there
  only, or a firewall can limit port 22 to it. Keep Calico on `eth0` (see
  CLAUDE.md).
- **Kea phase 3** (decision 5): a second Kea on another host as a hot-standby partner
  (the firewall's relay can point at both), and lease names into DNS.
- **Finish the second Pi-hole** (LXC 109 on a host other than the first's, `.4` on each
  network the first serves at `.3`, tagged `adblock`; kept the same by
  `prod/system/nebula-sync/`). Until clients are told about it, every one still asks only
  Pi-hole 1, and a reboot of its host takes DNS down. Left, in order: confirm nebula-sync's
  first sync (both answer the local records the same); add `.4` to Kea's
  `domain-name-servers` option per subnet (`/opt/kea/site/site.json` on the Docker VM, then
  `config-reload`); add `.4` to the k8s node LXCs' `nameserver` and the Docker VM's netplan
  nameservers; add the new host to Semaphore's Pi-hole inventory, create the "Pi-hole settings"
  variable group from Pi-hole 1's `misc.dnsmasq_lines`, and run `pihole-config.yml`
  (`ansible/README.md`). The
  DMZ network isn't served by Kea, so its static clients are changed by hand.
- **The Docker VM's host still carries DNS, DHCP, the VPN and Vaultwarden together**: a Kea
  HA partner on another host (decision 5) is what's left once both Pi-holes are in use.
- **Back up the Docker VM's databases to Garage**, the way the cluster's
  CloudNativePG databases already are. Today their only copy outside the VM is
  the Proxmox backup of its disks, which can catch a database mid-write:
  - `semaphore-postgres` (`docker/semaphore/`): Semaphore's own database,
    holding the inventories, SSH keys and secrets (encrypted with
    `SEMAPHORE_ACCESS_KEY_ENCRYPTION`, which isn't in the dump: keep it in
    Vaultwarden), and `tfstate`, OpenTofu's state for everything under `tofu/`.
  - `kea-postgres` (`docker/kea/`): Kea's reservations (all 50 live only
    there) and Stork's database.
  - Worth including, though not PostgreSQL: Vaultwarden's SQLite under
    `/opt/vaultwarden/data` (its `/admin` backup or `sqlite3 .backup`, never a
    plain copy of the live file) and Dockhand's data under `/opt/dockhand`.

  To design: a small container per stack (or one for the host) running
  `pg_dump -Fc` per database on a schedule, uploading to a `docker-backups`
  bucket in Garage (its own key, write-only if Garage allows it) with a
  retention, and alerting when a run fails; or WAL-G/pgBackRest for
  point-in-time recovery, which is likely more than these need. Done when each
  database has a fresh dump in Garage and one has been restored into a
  throwaway container and compared.
- **Ansible for the hosts outside the cluster**, so their hand-made setup
  can be rebuilt from git instead of from notes:
  - **Docker VM:** netplan (its three network legs, routing tables and rule
    priorities), the nft scripts and their systemd units (management return
    path, DMZ marks, the `DOCKER-USER` rules for Tailscale), sysctls, Docker,
    Dockhand's own compose file and the certificate-sync cron.
  - **Proxmox hosts:** repositories, packages, kernel cleanup, and the host
    settings the k8s LXCs depend on: kernel modules, sysctls, and the parts of
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

- [QUEUE-semaphore.md](QUEUE-semaphore.md): Semaphore for Ansible.
- [QUEUE-terraform.md](QUEUE-terraform.md): OpenTofu, Proxmox guests first.

A long job gets a `QUEUE-<task>.md` at the repo root; `CLAUDE.md` says when. A
finished one moves to `old-queues/`, whose README indexes what each settled and which checks
came back negative.
