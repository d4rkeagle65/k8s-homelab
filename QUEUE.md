# Homelab work queue

Outstanding work on the cluster and on this repo, roughly in the order worth doing it.
`CLAUDE.md` holds the rules for keeping it.

## Contents

- [Waiting on the owner](#waiting-on-the-owner)
- [Next](#next)
- [Cleanup](#cleanup)
- [Later](#later)
- [Working queue files](#working-queue-files) - one `QUEUE-<task>.md` per long job.

## Waiting on the owner

Decisions that unblock the work below; each gives a recommendation. Numbers stay as
assigned (items below refer to them); 1 to 6 and 8 to 10 are done, 7 waits on a setting.

- **Homepage's Dockhand login is Dockhand's admin** (`DOCKHAND_HOMEPAGE_USERNAME`/`_PASSWORD`,
  `services/homepage/`): Dockhand drives Docker through its socket, so its admin is root on the
  Docker host, and Homepage holds the password. Make a limited Dockhand user for the widget and
  put it in those two fields.
- **Sonarr: "Ms Rachel (2025)" was removed from TheTVDB** (its health page, 2026-10-19):
  re-point it to the new entry or remove it, or it stays an error and gets no updates.
- **The Synology's Docker install is untried with its share made first**
  (`ansible/synology-baseline.yml`): it's on DSM 7.1.1, the DS415+'s last, so its package is
  Docker 20.10.3, whose pre-install script failed from `synopkg` ("failed to create docker
  share", 2026-10-20). The playbook now makes the `docker` share itself before installing.
  The owner runs "Synology: baseline" again; should the install still fail, install Docker
  once from Package Center and run it again. Hawser is untried on DSM until then.
7. **Docker's live-restore, then Docker upgrades from Semaphore:** built 2026-10-16 in
   `ansible/docker-vm-update.yml`, both off until set (its header says what each does). The
   owner sets `docker_live_restore: true` on "Node: Docker Update" and runs it; then
   `upgrade_docker: true` once the live-restore run has read back on.

## Next

- **Grimmory loses a book's edits when Bookshelf moves its file** (found 2026-10-19): its
  library scan matches files by path, so a renamed or moved file is a deleted book and a new
  one; the old one's locked metadata (titles, authors, series), shelf entries and reading
  progress go with it. Its folder watching (`watch: true` in `sync/grimmory.yaml`) sees only
  changes made from its own pod, since the share is NFS, so it catches up at the nightly
  `SYNC_LIBRARY_FILES` task. On 2026-10-19 titles, authors and series were copied from
  Bookshelf into Grimmory and locked by a one-off script, not kept; after a Bookshelf rename
  pass the moved books lose that and need it again (Grimmory's metadata API, `PUT
  /books/{id}/metadata?replaceMode=REPLACE_WHEN_PROVIDED`), and re-shelving.
- **A failed Proxmox backup restarted a Talos worker** (found 2026-10-19): the backup job
  that ran 21:00-21:51 local on 2026-10-18 failed at 67% on VM 116 (a worker), and the
  host's task log then shows "VM 116 - Start": the VM's QEMU process had ended and Proxmox
  started it again. The node was NotReady about a minute (01:50-01:51:55 UTC, a new boot
  ID) and its pods came back by 01:55. Not memory: no host recorded an OOM kill. Find why
  the backup failed and the VM stopped in that host's journal (QEMU's exit, vzdump's error)
  around 21:50-21:52; left alone, each night's backup can take a worker down, and with it
  any database instance on its local-db disk. Whether the Talos workers need a VM backup
  at all is part of it: their system disk is rebuilt from config, and the databases on
  local-db already go to Garage.
- **Containers on the first Docker VM can't resolve local names** (found 2026-10-19): the
  claude-code container's resolver isn't a Pi-hole and forwards to public DNS, which answers
  NXDOMAIN for anything only the Pi-holes hold (an Ingress hostname, a host's name). Any
  container there that needs a local name fails the same way; check the others and the Docker
  daemon's `dns` setting (`ansible/docker-host.yml`, `ansible/tasks/resolver-cache.yml`).
- **rreading-glasses keeps only some of a new author's works** (upstream issue 602, open;
  found 2026-10-18): its first load of an author gets every work from Hardcover, but attaches
  them in batches that each start from the pre-refresh snapshot (`getAuthor` serves the
  `ra<id>` key while a refresh runs, by design per the declined PR 509), so the last batch
  overwrites the others and Bookshelf shows a short bibliography (Cassandra Clare: 3 books;
  six of eleven authors lost most works). Repaired by hand for all eleven: once a refresh has
  finished, a work fetched on its own attaches to the live record, so the missing works' cache
  rows (`w<id>` in `rreading-glasses-db`'s `cache` table) were deleted, the pod restarted (it
  also caches in memory) and each work fetched (`GET /work/<id>`), then RefreshAuthor in
  Bookshelf. The full list per author comes from its refresh's `ensuring author-work edges`
  lines with `VERBOSE=true`. Left: an author added from now on can come out short the same
  way; a small job that does this repair (or a patched image, or the fix upstream) would end
  it. Already-repaired authors keep their works across refreshes (the snapshot is complete).
  **Pace any repair to Hardcover's daily budget** (2026-10-18: about 2,000 repair lookups
  in an afternoon spent the token's whole day, `rg_gql_ratelimit_remaining{bucket="daily"}`
  0, and every uncached Bookshelf lookup failed for hours): read that metric before and
  during a batch and stop well short of 0; one work every few seconds, not one a second
  (rreading-glasses already keeps to the per-second burst budget; its pacing is
  hard-coded); never a repair while Bookshelf is identifying new files, or several large
  authors at once. The budget (Hardcover's response headers, 2026-10-19): 5,000 lookups a
  day, reset at 00:00 UTC, and 60 a minute with bursts of 10; a re-fetched work costs about
  one, so keep a day's repairs to about 1,000 and leave the rest to Bookshelf. The
  HardcoverDailyBudget alerts (homelab.bookshelf) ticket both the low and the spent budget.
- **systemd-resolved caches "no such name" on the Debian hosts** (found 2026-10-17): a hostname
  asked for before ExternalDNS made its record stayed unresolvable there though the Pi-holes
  answered it. `tasks/resolver-cache.yml` (in `baseline.yml`, 2026-10-17) sets
  `Cache=no-negative` where resolved runs; left: the owner's baseline run
- **The Pi-holes answer every network from Production** (found 2026-10-16, Run Command task
  784): from their management, DMZ and other addresses, replies leave by `eth0`, Production's
  default route, so a client on another subnet reaching one of those addresses through the
  firewall gets no answer (the asymmetric routing that hid the new Docker VM's SSH). They're
  containers whose networks Proxmox configures, so `tasks/management-routing.yml` only
  reports them: give each address its own table and rule (as the Docker VMs have), through
  the containers' network config, and check DMZ clients' DNS before and after.
- **Docker on the Docker VM was upgraded by "Node: Docker Update" (task 749, 2026-10-16), against
  its hold:** `docker-vm-update.yml` read the installed packages through the command module,
  which expanded `${Package}` as an environment variable, so its list to hold was empty and it
  upgraded docker-ce to 29.9.1, restarting the daemon and every container on the VM (Kea,
  Vaultwarden, Semaphore, NetBox, Tailscale; all back within seconds; Semaphore lost the task,
  which shows "running" forever). Fixed: the read goes through the shell, and the run stops
  unless every installed Docker package reads back held. Live-restore is still off: the run's
  `docker_live_restore` was a survey variable targeting "Environment", which Ansible never
  sees. Left: the survey's target set to "Variable", a dry run, then the run; task 749 stopped
  in Semaphore by the owner.
- **A LimitRange's defaults reach a Job's pods only when they run** (found 2026-10-16): the
  check before `cluster-policy` read the pods present, so Zammad's init job, gone by then, met
  the 512 MiB default only at its next upgrade and was OOM-killed. Before a change to the
  defaults, read each Job and CronJob template in the namespaces (and their past peaks in
  Prometheus), not only the running pods.
- **Containers still without health probes** (2026-10-16): NetBox's worker on the Docker VM
  (no port: an exec probe on the rq worker, in `docker/netbox/compose.yaml`). n8n's task
  runners (the launcher's `/healthz` on 5680) and External Secrets' controller (its chart's
  probes) got theirs; cert-manager's cainjector has no health endpoint (its 1.21 chart offers
  no probe); the config-reloader and dashboard sidecars, Calico, kube-proxy, Authentik's
  outposts and the tunnel connector belong to their charts and operators.
- **Pod Security: `restricted` where it holds.** Since 2026-10-16 every namespace whose pods
  pass enforces `baseline` (`pod-security.kubernetes.io/enforce` in its `namespace.yaml`; each
  checked by a server-side dry run of the label and its workload templates, CronJobs
  included). Left unlabelled, and so `privileged` (the control planes' cluster-wide
  default): calico-system, tigera-operator, monitoring, metallb-system, metrics-server,
  democratic-csi, homeassistant (host network, host paths, NET_RAW) and local-path-storage
  (its provisioner's helper pods mount host paths). Next: `restricted` for the 13 that pass it
  today (cert-manager, cluster-access, cnpg-system, external-dns, external-secrets,
  external-services, nebula-sync, redis-ha, renovate, smtp-relay, both Traefiks), once their
  CronJob and Job templates are checked against it too.
- **Prometheus doesn't scrape etcd** (`kube-prometheus-stack`'s `kubeEtcd` is off), so there's
  no WAL fsync latency, leader changes or database size history: on 2026-10-13 the Talos control
  plane's commit latency (26-31 ms against 9-25 ms) had to be sampled by hand. The control
  planes are all Talos since 2026-10-13, so etcd's metrics endpoint is set in one place, the
  Talos config (`talos/patches/`): add it there, then turn `kubeEtcd` on.
- **The second Proxmox host's NVMe drive (512 GB, the one carrying its guests) is at 86% of its
  rated wear** (`nvme_percentage_used_ratio`, 2026-10-10). The owner knows, decides when it's
  replaced, and chose what runs on it (a control plane and Pi-hole 1 since 2026-10-20): don't
  raise it again, and don't project its remaining life, which earlier estimates got wrong. The first host's 256 GB boot drive is at 66% (about 1.4 years). Every host's
  drives write 40 to 70 GB a day on lifetime average, far more than Prometheus did: once a few
  days of `node_disk_written_bytes_total` and `pve_disk_written_bytes` exist, find the writers
  (etcd, the workers' databases, images and logs, Proxmox's own files) and cut what can be.
  Alerts on wear and spare capacity are phase 5 of `QUEUE-monitoring.md`.

- **Kubernetes 1.37, when its patches settle:** `kubernetes_version` in
  `tofu/proxmox/talos-config.tf` in a commit, then `ansible/talos-k8s-upgrade.yml` (its dry run
  first), then the OpenTofu job. Check first that Talos 1.14 supports 1.37 (its support
  matrix) and the add-ons' ranges: Calico 3.33 is the one that covers 1.37 (3.33.0 tested on
  1.35-1.37). Bump the claude-code image's `KUBECTL_VERSION` once the cluster is on it.

## Cleanup

- **kea-standby's API doesn't answer an HTTP request from the owner's PC** (found 2026-10-20):
  on the second Docker VM's management address, port 8000 accepts the connection, then the
  `status-get` is reset after about 20 seconds, though the primary's HA heartbeats reach it
  (in touch, every few seconds). The first VM needed management-network return routing for
  the PC to reach its published ports; check the second has it. Left, a script on the PC
  that asks the standby (as `netbox_seed.py` asks the primary) gets no answer.

- **Whisparr v2's leftovers** (retired 2026-10-19; its final base backup
  `whisparr-db-final-202610191449` completed, its WAL archived through its end): from
  2026-10-20, delete the `whisparr-db` Cluster in `media` (kept from pruning by its
  annotation) and the released PV of `whisparr-config`, after reading that the backup is
  still listed in Garage.
- **`test-harness` reads red when the suite is run by hand in a Claude desktop worktree**
  (found 2026-10-18): the app's `config.worktree` points `core.hooksPath` at the main
  checkout's `.githooks`, whose `pre-merge-commit` runs `.githooks/pre-commit` from the
  worktree's top, but `other_calls_ours("pre-merge-commit")` in `tests/test-harness.py` accepts
  only a line naming `.githooks/pre-merge-commit`. The commit hook's own run passes. Left, a
  manual `run-all-tests.py` there fails for a hook that works.
- **`docker/claude-code/README.md`'s "Updating" says to change the image tag in `compose.yaml`**
  with the Dockerfile's versions, but the tag is always `homelab/claude-code:local` (built on
  the host, skipped by Renovate). Say instead what makes Dockhand rebuild after a Dockerfile
  change, once that's read on the host. Left, a version bump merged by Renovate may never
  reach the running image, and the README points at an edit that shouldn't be made.
- **`test-scripts-pytest` is red wherever `kubernetes/.local/` exists** (found 2026-10-20):
  `scripts/tests/test_dockhand_keyvault_copy.py`'s `"TIMEZONE"` fixture holds the real value,
  which `test_no_substitution_literal_leaks_outside_local` (`test_secrets_hygiene.py`) refuses.
  Use a made-up zone (`Etc/UTC`). Left, every full run and rebase on the owner's PC reads red,
  and a commit there touching `scripts/` is refused.
- **`talos_nodes_only` given as a string matches by substring** (`ansible/talos-upgrade.yml`,
  `n.metadata.name in talos_nodes_only`): the workers' common name prefix selects every worker. Split a string on
  commas and spaces into exact names, as `talos-reboot.yml` does with `reboot_vms`. Left, a
  partial name upgrades more nodes than meant (each still checked and drained first).
- **`test/media/jackett`** stays as a test-only app for now; not listed in
  `test/kustomization.yaml`.
- **Restore-test the PostgreSQL 18 clusters' backups.** Their base backups read `DONE` in Garage
  (2026-10-18), but none has been restored, and since the retired clusters' backups were
  deleted (the owner, 2026-10-18) they are the only copies. Recover each `<app>-db`, Kea's,
  rreading-glasses' and Zammad's into a one-instance copy and compare it table by table, as
  `old-queues/QUEUE-restore-tests.md` did for the old clusters. Left, a backup that can't be
  restored is found only when it's needed.
- **The retired databases' files may still be on the workers' `local-db` disks** (found
  2026-10-18): deleting a Retain PV leaves its folder on the node. The old clusters' PVs are
  gone, but nobody has looked for their folders, nor for NetBox's old database files
  (`QUEUE-autonomy.md`, released-volumes). Find each worker's `local-db` folders that no PV
  names, check the new cluster is healthy, then remove them with the owner's go-ahead. Left,
  they hold disk space on the workers.

## Later

- **Lidarr back to stable tags** (from 2026-10-18 it runs the nightly channel, pinned, for
  qBittorrent's API key; Renovate's `linuxserver/lidarr` rule follows nightlies only): once a
  stable release is past the nightly running, set `tag` in `prod/apps/media/lidarr/app/
  helmrelease.yaml` to it and remove that rule. Left alone, Lidarr stays on nightly builds.
- **Faster recovery from a node loss** (with the owner: a Talos control-plane change): every database now fails over in under a minute, but
  the app pods on a lost node wait out the default 5-minute
  `node.kubernetes.io/unreachable`/`not-ready` tolerations before moving. One place sets that
  for every pod: the API server's `--default-not-ready-toleration-seconds` and
  `--default-unreachable-toleration-seconds` (the DefaultTolerationSeconds admission plugin),
  in the Talos control planes' config: about 60 seconds, rolled node by node with `talosctl
  patch mc --mode=no-reboot` (tofu/CLAUDE.md), then the OpenTofu apply the owner confirms.
- **Proxmox host cleanup.**
  - Once kernel 7.0 has run cleanly for a couple of weeks (from about 2026-10-16), set
    `remove_old_kernels: true` on the Proxmox inventory and run the upgrade template: with the
    default `keep_kernels: 2` it removes `6.14.11-4` and keeps `6.14.11-9` as the fallback.
    Every run already lists what it would remove.
  - Don't run `zpool upgrade` unless a new ZFS feature is needed.

- **Pi-hole 1's sshd still doesn't come back after a container restart**, despite the
  hand-made drop-in (`ssh.service.d/20-after-network-online.conf`, 2026-10-10) meant to make
  sshd, which listens on the management address only, wait for the network: after a restart
  around 12:30 UTC on 2026-10-15 it refused connections until restarted by hand on 10-16,
  and every Semaphore run on Pi-hole 1 failed meanwhile. Find out why on the next restart
  (`journalctl -b -u ssh`; is the drop-in on both Pi-holes, and is
  `systemd-networkd-wait-online` or its equivalent enabled in the container?), then have
  `baseline.yml` own the fix on every host whose sshd sets `ListenAddress`.
- **Nothing owns the Pi-holes' timezone** (found 2026-10-19, Run Command task 874): CT 100
  and CT 109 differed until the owner set CT 109 to match by hand (`pct set`, both
  the `TIMEZONE` value since, task 887). Proxmox writes the zone into each container at start,
  but neither OpenTofu (the provider can't, `guests.tf`) nor a playbook sets it, so a rebuilt
  Pi-hole comes back with whatever its template had: `baseline.yml` or `pihole-config.yml`
  should own one zone for both.
- **Kea phase 3** (`QUEUE-kea-ha.md`; decision 5, the owner 2026-10-16: (b)): Kea's reservations move from
  PostgreSQL on the Docker VM to a CloudNativePG cluster reached through a LoadBalancer
  address, so a partner can read them while the Docker VM is down; then a second Kea on the
  second Pi-hole's host as a hot-standby partner (the firewall's relay can point at both), and
  lease names into DNS.
- **Both Pi-holes' DMZ addresses are in git history** (found 2026-10-19): QUEUE.md held them
  from `a0fc448` (2026-10-08) until `tests/test-identifying-data.py` caught them. The tree is
  clean; every clone and GitHub's history still have them. Private addresses, so low risk;
  rewriting history (as was done before) is the owner's call, and a no keeps them there.
- **DMZ clients don't know about the second Pi-hole**: Kea doesn't serve the DMZ, so its
  static clients still list only Pi-hole 1's DMZ address and lose DNS while Pi-hole 1's host
  reboots. Add Pi-hole 2's DMZ address (NetBox has both) on each by hand.
- **The Proxmox inventory's `evacuate` can point back at the host with the most free memory:**
  VT-x is on in every host (Run Command task 755, 2026-10-16: `vmx`, `kvm_intel` and `/dev/kvm`
  on all five), so the override that steered the Docker VM's evacuation away from the third host
  is no longer needed. Check the config context in NetBox, then drop it.
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
    packages. Still to add there: repositories and kernel cleanup.
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
- **OpenTofu for the rest of what's created through APIs** (decision 9; the Proxmox guests and
  Cloudflare's DNS records are done, `tofu/`, `old-queues/QUEUE-terraform.md`):
  - **Cloudflare:** the tunnel's public hostnames.
  - **The *arr settings:** download clients, root folders, auth and
    Prowlarr's app links, through the devopsarr providers. This is the
    alternative to a small in-cluster Job calling the apps' APIs, which
    needs no state file.
  - **Dockhand:** its Git credentials, the Git stacks it deploys from
    `docker/<stack>/` and their variables, plus its own settings and login,
    through the community `kalebharrison/dockhand` provider (unofficial,
    with known gaps, so a Dockhand update can break it). Dockhand can't set
    itself up: Ansible installs it on the VM, then Terraform configures it.
- **The PC's Claude sessions get API tokens of their own** (found 2026-10-20): local
  sessions on separate forks may be given copies of the claude-code container's
  `~/.semaphore-token` and `~/.netbox-token` (`/opt/claude-code/home/` on the Docker host),
  so one token serves two machines, and revoking it cuts off both. Make a Semaphore user and
  a NetBox token for the PC sessions instead, each revocable on its own, as the cluster
  access already is (`claude-code` ServiceAccount vs the PC's admin kubeconfig).

## Working queue files

- [QUEUE-autonomy.md](QUEUE-autonomy.md): read first: the owner's standing permissions, the batches I can do on my own, and what waits on the owner.
- [QUEUE-monitoring.md](QUEUE-monitoring.md) (about 55%): Prometheus, Loki and Grafana, in eight phases.
- [QUEUE-resource-limits.md](QUEUE-resource-limits.md) (about 85%): requests and limits on every container, in batches.
- [QUEUE-talos.md](QUEUE-talos.md) (about 93%): the nodes onto Talos VMs, surviving the loss of any host.
- [QUEUE-netbox.md](QUEUE-netbox.md) (about 95%): NetBox on the Docker VM as the source of Semaphore's hosts and OpenTofu's guests, then owning DNS.
- [QUEUE-kea-ha.md](QUEUE-kea-ha.md) (0%): Kea's reservations on CloudNativePG, then a hot-standby partner.
- [QUEUE-azure-keyvault.md](QUEUE-azure-keyvault.md) (about 65%): the cluster's and Dockhand's secrets from Azure Key Vault, one secret per variable.
- [QUEUE-secret-names.md](QUEUE-secret-names.md) (about 10%): one naming standard for Key Vault's secrets, and one secret wherever the cluster and Dockhand touch the same thing.
- [QUEUE-mariadb.md](QUEUE-mariadb.md) (0%): MariaDB through mariadb-operator, Grimmory moved onto it (no more dump script), then Youtarr.
- [QUEUE-zammad.md](QUEUE-zammad.md) (about 25%): Zammad, the helpdesk, with alerts becoming tickets.
- [QUEUE-updates.md](QUEUE-updates.md) (about 70%): version updates that change behaviour (Mealie, MetalLB,
  Calico, Authentik, app-template, PostgreSQL majors, Kubernetes), one at a time.

The percentages are estimates as of 2026-10-16, from each file's checklist weighed by the
size of what's left; update one when its file changes. A long job gets a `QUEUE-<task>.md` at
the repo root; `CLAUDE.md` says when. A
finished one moves to `old-queues/`, whose README indexes what each settled and which checks
came back negative.
