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

7. **Docker upgrades from Semaphore:** done for the packages: task 1043 upgraded Docker on both
   VMs with live-restore on (every container ran before and after: 20 and 3), and the packages
   are held again. Left: the owner reboots the first VM, which reports a reboot due, when
   Vaultwarden, Kea, Dockhand and Semaphore can be down.

## Next

- **The Immich import from OneDrive** (2026-10-21): Cloud Sync on the NAS brings the owner's
  OneDrive pictures (~50 GB) down into the media share's `oneDrive` folder, and
  `kubernetes/prod/apps/immich/immich-import/`'s CronJob uploads what Immich lacks, nightly (the
  Immich CLI, as the media apps' user, 1028:100, to the in-cluster Service). Left, in order: the
  owner adds `IMMICH-IMPORT-API-KEY` to Key Vault (an API key of the owner's own Immich
  account) and merges its `cluster-secrets` mapping, then the CronJob, which runs with
  `--dry-run`; a run started by hand (the command is in the CronJob's comment) and its log
  read: the CLI's "Found N new files and M duplicates", and any file it couldn't read; once
  Cloud Sync's first download is done, a pull request removes `--dry-run`, and a run started
  by hand does the bulk upload; then the owner reviews Immich's duplicates, and the run's
  length is read, since every run hashes the whole folder. Left with `--dry-run`, it reads
  the folder nightly and uploads nothing. Immich's server peaked at 5.3 GiB of its 6 GiB
  limit in the first import (`QUEUE-resource-limits.md`): an OOM kill fails the CLI's run, and
  the Job's retry carries on from what Immich already holds.
- **Grimmory loses a book's edits when Bookshelf moves its file** (found 2026-10-19): its
  library scan matches files by path, so a renamed or moved file is a deleted book and a new
  one; the old one's locked metadata (titles, authors, series), shelf entries and reading
  progress go with it. Its folder watching (`watch: true` in `sync/grimmory.yaml`) sees only
  changes made from its own pod, since the share is NFS, so it catches up at the nightly
  `SYNC_LIBRARY_FILES` task. On 2026-10-19 titles, authors and series were copied from
  Bookshelf into Grimmory and locked by a one-off script, not kept; after a Bookshelf rename
  pass the moved books lose that and need it again (Grimmory's metadata API, `PUT
  /books/{id}/metadata?replaceMode=REPLACE_WHEN_PROVIDED`), and re-shelving.
- **The nightly Proxmox backup could make the HA master's watchdog reset its host** (found
  2026-10-19, cause 2026-10-21, Run Command tasks 1010-1016): on 2026-10-18 every host's vzdump
  wrote to the TerraMaster's `tm-nfs` (mounted `hard`) at once; the NAS fell behind, the first
  HP host's writes backed up until even journald stopped, and as HA CRM master (watchdog
  armed) it was reset, taking its worker with it. Changed 2026-10-21 in the hand-made job
  (Datacenter, Backup; not in git): the four Talos workers (VMs 114-117) are excluded, their
  data being replicated and in Garage, and each host's limit is 25 MiB/s, down from 50; the
  owner set its retention to 7 daily, 4 weekly, 3 monthly, 1 yearly. Left: check the next
  nights' runs (each host's vzdump task OK, no "nfs: server not responding" on any host, no
  CRM master change at 21:00-23:30); if the NAS still falls behind, run the hosts one after
  another. The workers' old archives stay on `tm-nfs`, since the job no longer prunes them:
  delete them once the owner no longer wants them.
- **rreading-glasses keeps only some of a new author's works** (upstream issue 602, open;
  found 2026-10-18): its first load of an author attaches the works in batches that each start
  from the pre-refresh snapshot (the `ra<id>` key, by design per the declined PR 509), so the
  last batch overwrites the others and Bookshelf shows a short bibliography. The
  `rreading-glasses-repair` CronJob (`kubernetes/prod/apps/media/rreading-glasses/app/`, its
  script's docstring says how) finds every Bookshelf author missing works it has cached,
  without a Hardcover lookup, and repairs them through upstream's own path (`DELETE` then
  `GET /work/<id>`), paced to Hardcover's daily budget. It starts with `DRY_RUN` "true"; a
  dry run from a session on 2026-10-21 found 4 authors short by 27 works in all. Left, in
  order: the owner merges it; a night's run (or one started by hand, the command is in the
  CronJob's comment) is read: each short author and its count; a pull request sets `DRY_RUN`
  "false", and the next run's log shows each work attached and the budget it spent (its
  `DELETE` path has never been run here: the script stops if the work doesn't expire, and
  fails if a work doesn't attach). Left in dry run, a new author's missing works are only
  listed in the log.
  **Pace any repair by hand to Hardcover's daily budget** (2026-10-18: about 2,000 repair
  lookups in an afternoon spent the token's whole day, and every uncached Bookshelf lookup
  failed for hours): read `rg_gql_ratelimit_remaining{bucket="daily"}` before and during a
  batch and stop well short of 0; one work every few seconds; never while Bookshelf is
  identifying new files, or several large authors at once. The budget (Hardcover's response
  headers, 2026-10-19): 5,000 lookups a day, reset at 00:00 UTC, and 60 a minute with bursts
  of 10; a re-fetched work costs about one, so keep a day's repairs to about 1,000. The
  HardcoverDailyBudget alerts (homelab.bookshelf) ticket both the low and the spent budget.
- **A LimitRange's defaults reach a Job's pods only when they run** (found 2026-10-16): the
  check before `cluster-policy` read the pods present, so Zammad's init job, gone by then, met
  the 512 MiB default only at its next upgrade and was OOM-killed. Before a change to the
  defaults, read each Job and CronJob template in the namespaces (and their past peaks in
  Prometheus), not only the running pods.
- **Pod Security: `restricted` for the rest.** `pod-security.kubernetes.io/enforce` in each
  `namespace.yaml`: 14 namespaces enforce `restricted` (cert-manager, cluster-access,
  cnpg-system, external-dns, external-secrets, external-services, headlamp, kea, nebula-sync,
  redis-ha, renovate, smtp-relay, both Traefiks), 21 `baseline`. Left unlabelled, and so
  `privileged` (the control planes' cluster-wide default): calico-system, tigera-operator,
  monitoring, metallb-system, metrics-server, democratic-csi, homeassistant (host network,
  host paths, NET_RAW) and local-path-storage (its provisioner's helper pods mount host
  paths). The `baseline` ones fail `restricted` on their containers' security contexts
  (`allowPrivilegeEscalation`, capabilities not dropped, `runAsNonRoot`, seccomp), Grimmory on
  a volume type too, and Immich while `immich-import` exists (an inline NFS volume, as
  Grimmory's): each needs its app's own settings changed, one app at a time. Check a
  namespace both ways before raising it: a server-side dry run of the label (`kubectl label
  --dry-run=server --overwrite ns <ns> pod-security.kubernetes.io/enforce=restricted`), which
  judges only the pods running; and every pod template it can start, each made a Pod and
  dry-run created in a throwaway namespace enforcing `restricted`: Deployments, StatefulSets
  (claim templates as volumes), DaemonSets, Jobs, CronJobs, Helm hooks (`helm get hooks`, Jobs
  that exist only during an upgrade) and an operator's Jobs (CloudNativePG's share its
  instance pods' security contexts).
- **The second Proxmox host's NVMe drive (512 GB, the one carrying its guests) is at 86% of its
  rated wear** (`nvme_percentage_used_ratio`, 2026-10-10). The owner knows, decides when it's
  replaced, and chose what runs on it (a control plane and Pi-hole 1 since 2026-10-20): don't
  raise it again, and don't project its remaining life, which earlier estimates got wrong. The first host's 256 GB boot drive is at 66% (about 1.4 years). Every host's
  drives write 40 to 70 GB a day on lifetime average, far more than Prometheus did: once a few
  days of `node_disk_written_bytes_total` and `pve_disk_written_bytes` exist, find the writers
  (etcd, the workers' databases, images and logs, Proxmox's own files) and cut what can be.
  Found 2026-10-21 (7-day rates): the hosts' guest drives write 40 to 147 GB a day; the guests
  about 280 of the 470, the rest Proxmox's own. The CloudNativePG instances were the largest,
  about 4.5 GB a day each from forced 5-minute WAL switches (now hourly, `archive_timeout: 1h`).
  Left: each control plane writes 26 to 30 GB a day (its kube-apiserver about 5, the rest
  etcd, which isn't a pod: read it from the etcd scrape, `QUEUE-monitoring.md` phase 5); both Prometheus
  replicas about 7 each; the first Docker VM 21; and the hosts' own share. Remeasure a week
  after the archive change.
  Alerts on wear and spare capacity are phase 5 of `QUEUE-monitoring.md`.

- **Kubernetes 1.37, when its patches settle:** `kubernetes_version` in
  `tofu/proxmox/talos-config.tf` in a commit, then `ansible/talos-k8s-upgrade.yml` (its dry run
  first), then the OpenTofu job. Check first that Talos 1.14 supports 1.37 (its support
  matrix) and the add-ons' ranges: Calico 3.33 is the one that covers 1.37 (3.33.0 tested on
  1.35-1.37). Bump the claude-code image's `KUBECTL_VERSION` once the cluster is on it.

- **Remove the older host-selection names** (2026-10-21): every Semaphore template's survey sends
  `target_hosts` (`ansible/select-hosts.yml`), and dry runs passed under it: "Reboot: Proxmox
  Hosts" with `ALL` (task 1081: all five hosts, Flux's hold planned and lifted), "Host: Proxmox
  Upgrade" (1082) and "Node: Upgrade Talos" (1083, every node already on the target, so its
  per-node steps didn't run). Left: a pull request removes the older names (`select_alias` in
  `proxmox-reboot.yml`, `run-command.yml` and `docker-host.yml`; `reboot_vms` in
  `talos-reboot.yml`, `talos_nodes_only` in `talos-upgrade.yml`). Left as is, two names say
  one thing.

## Cleanup

- **The old Grimmory database's dump folder on the Synology** (2026-10-21, the owner's to
  delete): `grimmory-grimmory-db-backup-pvc-5df26534-…` under the NFS share's `k8sData`, the
  nightly `mariadb-dump`s of the database #225 retired (its PV is gone; the data is superseded
  by `grimmory-db`'s physical backups). Left, 14 days of stale dumps take NAS space.

- **The CloudNativePG instances crowd two of the four workers** (found 2026-10-21): of 31
  instances, one worker runs 13 and another 11, the other two 3 and 4, since each cluster's
  anti-affinity only keeps its own two apart. Each drain also moves primaries off and nothing
  moves them back: after the Talos 1.14.4 run one worker held 13 of the 16 primaries. Seven
  were switched back on 2026-10-21 (20-30 s each, only Zammad's scheduler restarted), to 6, 3,
  3 and 4: as even as the instances allow, since a primary can only move to its replica's node.
  Left, losing the busiest worker fails over six databases and restarts 13 instances, and the
  next full drain piles the primaries up again. Spread the instances (a topology spread across
  the workers, or moving replicas one at a time onto the emptier ones), and end a run that
  drains every worker by switching primaries back out.

- **NetBox's change-log API fails on Topology Views' role icons** (found 2026-10-21): the
  plugin's `RoleImageSerializer` (`netbox-topology-views` 4.7.0, `api/serializers.py`) is a plain
  DRF `ModelSerializer`, so NetBox 4.7 rendering a change's `changed_object` raises "unexpected
  keyword argument 'nested'" and `/api/core/object-changes/` answers 500 for any page holding a
  role-icon change (eight, from setting the icons). `?fields=` without `changed_object` works.
  Nothing here reads the change log by API today; a script that does would fail until NetBox's
  housekeeping drops those rows at the retention limit. Report it upstream, or patch the
  serializer in `docker/netbox/Dockerfile` until a release fixes it.

- **`test-script-health`'s standard-library check only knows the platform it runs on**
  (found 2026-10-20): `t.probe` asks the running Python whether each called name exists, so a
  POSIX-only name (`os.uname`, in #168's `ansible/files/proxmox-backup-metrics.py`) passes in
  the Linux container and refuses every commit on the owner's Windows PC, and a Windows-only
  one would do the reverse. Give it a list of names that exist on only one platform, or let a
  script that runs only on Linux hosts say so. Left, the next one again blocks commits on
  whichever machine didn't write it.

- **kea-standby's API doesn't answer an HTTP request from the owner's PC** (found 2026-10-20):
  on the second Docker VM's management address, port 8000 accepts the connection, then the
  `status-get` is reset after about 20 seconds, though the primary's HA heartbeats reach it
  (in touch, every few seconds). The first VM needed management-network return routing for
  the PC to reach its published ports; check the second has it. Left, a script on the PC
  that asks the standby (as `netbox_seed.py` asks the primary) gets no answer.

- **`test-harness` reads red when the suite is run by hand in a Claude desktop worktree**
  (found 2026-10-18): the app's `config.worktree` points `core.hooksPath` at the main
  checkout's `.githooks`, whose `pre-merge-commit` runs `.githooks/pre-commit` from the
  worktree's top, but `other_calls_ours("pre-merge-commit")` in `tests/test-harness.py` accepts
  only a line naming `.githooks/pre-merge-commit`. The commit hook's own run passes, since it
  runs only the suites for the staged files. Left, a manual `run-all-tests.py` there fails for
  a hook that works. So does the post-rewrite hook after every rebase that rewrites a commit,
  which prints "the rebased branch is red" for a clean change and invites a needless
  `git reset --keep ORIG_HEAD`.
- **`docker/claude-code/README.md`'s "Updating" says to change the image tag in `compose.yaml`**
  with the Dockerfile's versions, but the tag is always `homelab/claude-code:local` (built on
  the host, skipped by Renovate). Say instead what makes Dockhand rebuild after a Dockerfile
  change, once that's read on the host. Left, a version bump merged by Renovate may never
  reach the running image, and the README points at an edit that shouldn't be made.
- **`test/media/jackett`** stays as a test-only app for now; not listed in
  `test/kustomization.yaml`.
- **The retired databases' files may still be on the workers' `local-db` disks** (found
  2026-10-18): deleting a Retain PV leaves its folder on the node. The old clusters' PVs are
  gone, but nobody has looked for their folders, nor for NetBox's old database files
  (`QUEUE-autonomy.md`, released-volumes). Find each worker's `local-db` folders that no PV
  names, check the new cluster is healthy, then remove them with the owner's go-ahead. Left,
  they hold disk space on the workers.

## Later

- **Point-in-time recovery for Grimmory's MariaDB** (`old-queues/QUEUE-mariadb.md`): on
  mariadb-operator 26.10.1 the agent logged "Starting binary log archiver" and archived nothing
  to Garage, added to a running MariaDB or configured from creation. Retry on a later operator
  release, on a scratch MariaDB first; until then a restore goes back to the nightly physical
  backup (03:40), up to a day of Grimmory's changes.

- **A new private value's first promotion is #189's live check** (`scripts/promote.py`,
  `varsub.pending_rows`): the next Secret capture promotes should list its row in
  `kubernetes/.local/keyvault-pending.yaml` plain, with its `template` entry, and its
  manifest be written once the value is in Key Vault and mapped with that entry. A row still
  base64 for a value that is text means capture's `secret_data` mark didn't reach
  `varsub.pending_rows`. Left, a new value can go into Key Vault base64 and reach its app
  encoded twice.
- **`scripts/keyvault-secrets.ps1` shows Dockhand's secrets as `Synced` `no`** (found 2026-10-21):
  the 39 names only Dockhand stacks read (`docker/secret-names.yaml`) aren't mapped into
  `cluster-secrets`, rightly, and the listing marks them as it marks a cluster variable that
  never reaches the cluster. Read the map and say `Dockhand` for them. Left, the listing reads as
  39 faults, which hides a real one among them.
- **A promoted Secret's value that isn't text has no supported form**: Key Vault holds only
  text, and `scripts/tests/test_structure.py`'s `test_every_substituted_variable_is_defined`
  refuses a Secret's variable without its `{{ .NAME | b64enc }}` template entry (capture's
  pending note says so). None exists today. One route, untried: Key Vault holds its base64,
  and its `cluster-secrets.yaml` entry adds `decodingStrategy: Base64` to `remoteRef`, so the
  usual template line gives the Secret that base64 back; the structure test's
  `remoteRef == {key: ...}` check and `keyvault-secrets.ps1`'s listing would need to allow it.
  Left, the first such Secret can't be promoted.

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

- **The sshd retry drop-in isn't proven yet** (`ansible/tasks/ssh-listen-retry.yml`, in
  `baseline.yml`): Pi-hole 1's sshd failed its bind at a container start (exit 255, which
  Debian's `ssh.service` never retries) and stayed down until restarted by hand. Run the
  baseline, then restart Pi-hole 1's container: `journalctl -b -u ssh` should show the failed
  bind, then "Server listening" with no one touching it. Left unproven, a Pi-hole restart
  can still cut Semaphore off from it.
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
- [QUEUE-kea-ha.md](QUEUE-kea-ha.md) (0%): Kea's reservations on CloudNativePG, then a hot-standby partner.
- [QUEUE-azure-keyvault.md](QUEUE-azure-keyvault.md) (about 75%): the cluster's and Dockhand's secrets from Azure Key Vault, one secret per variable.
- [QUEUE-zammad.md](QUEUE-zammad.md) (about 25%): Zammad, the helpdesk, with alerts becoming tickets.
- [QUEUE-updates.md](QUEUE-updates.md) (about 70%): version updates that change behaviour (Mealie, MetalLB,
  Calico, Authentik, app-template, PostgreSQL majors, Kubernetes), one at a time.

The percentages are estimates as of 2026-10-16, from each file's checklist weighed by the
size of what's left; update one when its file changes. A long job gets a `QUEUE-<task>.md` at
the repo root; `CLAUDE.md` says when. A
finished one moves to `old-queues/`, whose README indexes what each settled and which checks
came back negative.
