# Homelab work queue

Outstanding work on the cluster and on this repo, roughly in the order worth doing it.
`CLAUDE.md` holds the rules for keeping it. Database backups are handled outside this repo, so
nothing here tracks them.

## Contents

- [Waiting on the owner](#waiting-on-the-owner)
- [Next](#next)
- [Cleanup](#cleanup)
- [Later](#later)
- [Working queue files](#working-queue-files) - `QUEUE-semaphore.md`, `QUEUE-k8s-upgrade.md`.

## Waiting on the owner

Decisions that unblock the work below; each gives a recommendation.

1. **CloudNativePG in-place updates** (branch `addon/cnpg-inplace-updates`): turn on
   `ENABLE_INSTANCE_MANAGER_INPLACE_UPDATES` before the four operator upgrades, so each upgrade
   swaps the instance manager inside running pods instead of restarting all 11 databases (and
   taking immich and manictime down) four times. Recommended: yes, merged first.
2. **metrics-server into Flux:** the Helm chart reproduces the live Deployment with
   `args: [--kubelet-insecure-tls]`, `hostNetwork.enabled: true`, `containerPort: 4443`, but its
   selector differs, so the Deployment is deleted and recreated (about a minute without
   `kubectl top`), and the old `system:aggregated-metrics-reader` ClusterRole goes by hand.
   The alternative is the upgrade playbook applying the official manifest per bundle, outside
   git. Recommended: Flux (one owner, upgraded by commits like the rest).
3. **Calico into Flux** (plan in `QUEUE-k8s-upgrade.md`): go-ahead, and first a new
   `cluster-secrets` field for the pod network range (the chart values carry it; capture then
   writes `${CALICO_POD_CIDR}` in its place). Not needed before the 1.35 step.
4. **The second Pi-hole:** which Proxmox host (not the one with the first Pi-hole and the
   Docker VM; the host with the most free memory is also the Docker VM's evacuation target,
   and one other has little free), its addresses on each VLAN the first one serves, and how to
   keep the two the same (recommended: `nebula-sync`, which copies Pi-hole v6's settings,
   lists and local records through its API, as a container on the Docker VM).
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
7. **Docker's live-restore on the Docker VM** (`"live-restore": true` in
   `/etc/docker/daemon.json`): containers keep running while the Docker daemon restarts, so
   `docker-vm-update.yml` could upgrade Docker too instead of leaving it for a run by hand.
   Recommended: yes.
8. **Keep immich and manictime up through a node loss** (item below): `instances: 2` doubles
   their database disk on another worker. With in-place updates (1.) the remaining downtime is
   a node's drain. Recommended: yes for immich (photos), optional for manictime.
9. **Terraform/OpenTofu state** (item below): where the state lives. It holds secrets, and
   what it describes includes the cluster nodes, so not inside the cluster. Recommended: a
   `tfstate` database in Semaphore's PostgreSQL on the Docker VM (OpenTofu's `pg` backend), run
   from Semaphore's OpenTofu support.
10. **The two test folders** (item below): move `scripts/tests` under `tests/` with one runner,
    or keep both and say why in each folder's CLAUDE.md. Recommended: keep both; the generator's
    suite needs its own fixtures and runs offline, the gate is repo-wide.

## Next

- **Semaphore for Ansible, starting with keeping the Proxmox hosts up to date.** In progress;
  see [QUEUE-semaphore.md](QUEUE-semaphore.md).
- **Kubernetes 1.33 is past end of life: node updates and version upgrades to 1.36 through
  Ansible**, with version bundles checked for compatibility; CRI-O on the nodes is a dev build
  from a frozen repository. In progress; see [QUEUE-k8s-upgrade.md](QUEUE-k8s-upgrade.md).

## Cleanup

- **`test/media/jackett`** stays as a test-only app for now; not listed in
  `test/kustomization.yaml`.

- **generate copies `values.yaml` comments into `app/helmrelease.yaml` at the wrong
  indentation** (`generate.py`, where it reads `values.yaml` with `yamlio.read_yaml_file`): a
  comment over a nested key lands at the parent's indentation. YAML ignores it, but the file
  reads as if the comment belonged elsewhere. Strip comments when embedding, or keep their
  indentation. Until then, put explanations in the commit message, not in `values.yaml`.

## Later

- **Keep immich and manictime up through a node loss** (decision 8). Today
  their single database copy sits on one worker's disk.
  - Set `instances: 2` on `immich-postgres` and `manictime-pg`, for automatic
    failover in under a minute.
  - Shorten the app pods' `node.kubernetes.io/unreachable`/`not-ready`
    tolerations from the default 5 minutes to about 30 seconds.
  - Cost: double the database disk on another worker.
- **Proxmox host cleanup.**
  - Run `apt autoremove` on each host to clear the Proxmox 8 leftovers.
  - Once kernel 7.0 has run cleanly for a couple of weeks, remove
    `6.14.11-4` (keep `6.14.11-9` as the fallback): `/boot` is only 456 MB.
  - Don't run `zpool upgrade` unless a new ZFS feature is needed.

- **SSH to the k8s nodes only from the management network** (decision 6). Each node
  now has a management-network interface (`eth1`), so sshd can listen there
  only, or a firewall can limit port 22 to it. Keep Calico on `eth0` (see
  CLAUDE.md).
- **Kea phase 3** (decision 5): a second Kea on another host as a hot-standby partner
  (the firewall's relay can point at both), and lease names into DNS.
- **One Proxmox host carries both Pi-hole and the Docker VM** (decision 4), so its
  reboot takes DNS, DHCP, the VPN and Vaultwarden down
  together. A second Pi-hole and a Kea HA partner on other hosts would fix
  that.
- **Ansible for the hosts outside the cluster**, so their hand-made setup
  can be rebuilt from git instead of from notes:
  - **Docker VM:** netplan (its three network legs, routing tables and rule
    priorities), the nft scripts and their systemd units (management return
    path, DMZ marks, the `DOCKER-USER` rules for Tailscale), sysctls, Docker,
    Dockhand's own compose file and the certificate-sync cron.
  - **Proxmox hosts:** repositories, packages, kernel cleanup, and the host
    settings the k8s LXCs depend on.
  - **Pi-hole:** its `pihole-FTL --config` settings (the `local=` lines,
    HTTPS filtering, no conditional forwarding, `bogusPriv`) and the
    hand-made local records such as `vaultwarden-direct`.

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

  Before starting: decide where the state file lives (it holds secrets,
  including the stacks' secret variables, so not this repo) and what runs it
  (by hand, CI, or tofu-controller under Flux). Import the existing resources
  first, so nothing gets recreated.

- **Consolidate the two test folders** (low priority; decision 10). `tests/` is the repo-wide gate
  (doc-style, harness, hygiene, script-health suites, run by `tests/run-all-tests.py` and the
  pre-commit hook); `scripts/tests/` is the generator's pytest suite, reached only through
  `tests/test-scripts-pytest.py`. Either move the generator's tests under `tests/` (say
  `tests/scripts/`) with one runner, or leave the layout and make the split obvious in
  `tests/CLAUDE.md` and `scripts/CLAUDE.md`. Left as is, a new test can land in the wrong
  folder and skip the checks it was meant for.

## Working queue files

- [QUEUE-semaphore.md](QUEUE-semaphore.md): Semaphore for Ansible.
- [QUEUE-k8s-upgrade.md](QUEUE-k8s-upgrade.md): Kubernetes node updates and version upgrades.

A long job gets a `QUEUE-<task>.md` at the repo root; `CLAUDE.md` says when. A
finished one moves to `old-queues/`, whose README indexes what each settled and which checks
came back negative.
