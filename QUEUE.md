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

- **Delete the old single-version Hyper Backup copy on the TerraMaster** (2026-10-22): the
  cancelled first run of the single-version `k8s-to-tnas` task left `k8sData_synology/` in the
  `k8sBackups` share; the multi-version task's `k8sData_synology.hbk` beside it succeeded on
  2026-10-21, so nothing reads the old folder. Delete only that folder, never `garage/` (every
  database backup) or `k8sData_synology.hbk`. Left, it holds a partial copy of the Synology's
  `k8sData` taking TerraMaster space.

- **Report the TerraMaster's SNMP setting to TerraMaster** (from the monitoring work): TOS's SNMP
  page doesn't reach `/etc/snmp/snmpd.conf` (snmpd kept `rocommunity public`), so line 33 holds
  a hand-edited community limited to the management network. An update, a reboot or saving SNMP
  in TOS's UI can rewrite it; `TerraMasterSNMPDown` then alerts, and the line is edited again.

Decisions that unblock the work below; each gives a recommendation. Numbers stay as
assigned (items below refer to them); 1 to 10 are all done (7, Docker's upgrade from
Semaphore, with the first VM's reboot on 2026-10-22).

## Next

- **Vaultwarden's mail from a shared mailbox** (2026-10-22): its `SMTP_FROM` reads
  `M365_SHARED_MAILBOX_ADDRESS` (#316), and the redeploy is read back (equal by hash to Key
  Vault's). The Entra app ("SMTP OAuth Relay") has no Graph permission: its send rights are an
  Exchange RBAC for Applications role assignment whose management scope named only the owner's
  mailbox. Left: the owner widens that scope to the shared mailbox (`Set-ManagementScope`,
  checked with `Test-ServicePrincipalAuthorization`), then a test mail from Vaultwarden's admin
  page, read back in the shared mailbox's Sent Items; the stack's leftover `OWNER_EMAIL`
  variable deleted in Dockhand; Databasus's notifier From changed in its UI
  (`docker/databasus/README.md`); then the scope narrowed to the shared mailbox alone. Until the
  scope takes, Graph refuses every mail Vaultwarden sends.
- **The Immich import from OneDrive** (2026-10-21): Cloud Sync on the NAS brings the owner's
  OneDrive pictures down into the media share's `oneDrive` folder, and
  `kubernetes/prod/apps/immich/immich-import/`'s CronJob uploads what Immich lacks, nightly (the
  Immich CLI, as the media apps' user, 1028:100, to the in-cluster Service). Its dry run
  (2026-10-21, 9 minutes): 9,264 files (52.4 GB), 4,193 already in Immich byte for byte, 5,071
  new (23.1 GB), none unreadable. The bulk upload (2026-10-22, #273 took `--dry-run` off): all
  5,071 accounted for, in about 46 minutes from 01:13 to 01:58 UTC with two restarts between.
  The first Job uploaded 4,201 before its own container was OOM-killed at 1Gi after about 25
  minutes, 13.5 of 23.1 GB sent (the CLI holds each file in memory while it uploads it, and
  the folder has videos of 2.9 GB, 1.1 GB and 933 MB). A one-off Job at 4Gi lost its retries in
  a minute to Immich's database restarting under #258's spread, and a second ran clean in 15
  minutes: 861 uploaded (6.1 GB), 9 skipped as content Immich already had, peak 2.13 GiB.
  Immich's server didn't OOM. Left: #276 (the CronJob at 4Gi) merged; once Immich's job queues
  are empty, the owner reviews Immich's duplicates (re-encoded copies the hash can't match);
  and a nightly run's length read, since every run hashes the whole folder (about 10 minutes
  here).
- **Dockhand's Restart of `kea` leaves `stork-agent` stopped** (found 2026-10-22): after compose
  has started the stack, Dockhand restarts its containers once more, and `stork-agent`, which
  shares `kea-dhcp4`'s network and PID namespaces, dies with it and fails to come back ("cannot
  join network namespace of a non running container"; Dockhand logs "1 orphan container(s)
  failed to restart"). Redeploy kea instead, which runs `compose up`. Left: report it to Dockhand
  (github.com/Finsys/dockhand) with that log line; until then, a Restart leaves Stork blind to
  Kea while DHCP runs on.
- **Turn on Grimmory's relink job once a dry run has been read** (2026-10-21): Grimmory's
  library scan (`SYNC_LIBRARY_FILES`, 01:00) matches files by path, so a file Bookshelf moved
  is a deleted book and a new one, and the deleted book's locked metadata, shelves, reading
  progress, notes and Kobo state go with it (hard-deleted: `BookDeletionService` in 3.5.0).
  Grimmory recognises a move by its content hash only when its folder watcher sees it, and
  the watcher sees only its own pod's changes, not Bookshelf's over NFS. The
  `grimmory-relink` CronJob (`kubernetes/prod/apps/grimmory/grimmory/app/`, hourly at :50)
  does what the watcher would: a database file missing on the share whose hash belongs to
  exactly one unclaimed file gets that file's folder and name, so the book keeps its id and
  everything on it. It starts with `DRY_RUN` "true". Left: after a Bookshelf rename, run it
  by hand (the command is in the CronJob's comment) before 01:00 and read its "would move"
  lines; then a pull request sets `DRY_RUN` "false". Not covered, and reported only: an upgrade
  (Bookshelf replacing a book's file with a different one) is new content, so the scan still
  replaces that book. Left as is, every Bookshelf rename pass costs the moved books their edits.
- **The nightly Proxmox backup could make the HA master's watchdog reset its host** (found
  2026-10-19, cause 2026-10-21, Run Command tasks 1010-1016): on 2026-10-18 every host's vzdump
  wrote to the TerraMaster's `tm-nfs` (mounted `hard`) at once; the NAS fell behind, the first
  HP host's writes backed up until even journald stopped, and as HA CRM master (watchdog
  armed) it was reset, taking its worker with it. Changed 2026-10-21 in the hand-made job
  (Datacenter, Backup; not in git): the four Talos workers (VMs 114-117) are excluded, their
  data being replicated and in Garage, and each host's limit is 25 MiB/s, down from 50; the
  owner set its retention to 7 daily, 4 weekly, 3 monthly, 1 yearly. The first night after
  (2026-10-22, 01:00-03:12 UTC) was clean on every host. Left: check a few more nights' runs
  (each host's vzdump task OK, no "nfs: server not responding" on any host, no
  CRM master change at 21:00-23:30); if the NAS still falls behind, run the hosts one after
  another. (The workers' old archives, and those of every guest that no longer exists, were
  deleted on 2026-10-22: `tm-nfs` went from 4.1 to 2.2 TB.)
- **rreading-glasses keeps only some of a new author's works** (upstream issue 602, open;
  found 2026-10-18): its first load of an author attaches the works in batches that each start
  from the pre-refresh snapshot (the `ra<id>` key, by design per the declined PR 509), so the
  last batch overwrites the others and Bookshelf shows a short bibliography. The
  `rreading-glasses-repair` CronJob (`kubernetes/prod/apps/media/rreading-glasses/app/`, its
  script's docstring says how) finds every Bookshelf author missing works it has cached,
  without a Hardcover lookup, and repairs them through upstream's own path (`DELETE` then
  `GET /work/<id>`), paced to Hardcover's daily budget. Its first nightly dry run
  (2026-10-22 07:15 UTC) listed 4 authors short by 27 works, as the session's check had; it
  now repairs (`DRY_RUN` "false"). The first repair, started by hand 2026-10-22, attached 26
  of the 27 (27 lookups; 4,973 of the day's budget left), so `DELETE` then `GET` works. One
  didn't: work 2813965 of Hardcover author 132049 (Bookshelf author 10). Its cached record
  names that author on all 99 editions, and no work of the same title is on the author's
  record, yet the record (295 works) lacks it, so the job failed as designed. Left: read the
  next nightly run (it tries that work again, one lookup); if it still won't attach, look at
  how rreading-glasses attaches a work whose author record was refreshed after it (upstream
  issue 602's area). Until then the job fails each night on it, and KubeJobFailed tickets it.
  **Pace any repair by hand to Hardcover's daily budget** (2026-10-18: about 2,000 repair
  lookups in an afternoon spent the token's whole day, and every uncached Bookshelf lookup
  failed for hours): read `rg_gql_ratelimit_remaining{bucket="daily"}` before and during a
  batch and stop well short of 0; one work every few seconds; never while Bookshelf is
  identifying new files, or several large authors at once. The budget (Hardcover's response
  headers, 2026-10-19): 5,000 lookups a day, reset at 00:00 UTC, and 60 a minute with bursts
  of 10; a re-fetched work costs about one, so keep a day's repairs to about 1,000. The
  HardcoverDailyBudget alerts (homelab.bookshelf) ticket both the low and the spent budget.
- **Pod Security: three more namespaces to `restricted`, the rest stay `baseline`** (the owner,
  2026-10-22). In PR: elastic-system, mesh-mini and zammad, whose pods lacked only a seccomp
  profile (and dropped capabilities on Zammad's memcached); their new pod templates, dry-run
  as Pods under `restricted`, pass, and the live ones fail on exactly those fields. Zammad's
  Helm test hook `zammad-test-connection` fails `restricted`, but runs only on `helm test`.
  The other 18 `baseline` namespaces stay there by decision: their images would have to run
  as a non-root user, a per-app change that risks breaking each for a modest gain. The eight
  unlabelled (`privileged`) ones need host access. Left: the merge, then each of the three
  read back (`kubectl label --dry-run=server` warns of nothing, its pods restarted and ready).
  Before raising any namespace later, check every pod template it can start, not only the
  running pods: Deployments, StatefulSets, DaemonSets, Jobs, CronJobs, Helm hooks and an
  operator's Jobs, each dry-run as a Pod in a namespace that enforces `restricted` (its
  `serviceAccountName` removed, or the dry run fails on that instead).
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
  etcd, which isn't a pod: read it from the etcd scrape, `old-queues/QUEUE-monitoring.md` phase 5); both Prometheus
  replicas about 7 each; the first Docker VM 21; and the hosts' own share. Remeasure a week
  after the archive change.
  Alerts on wear and spare capacity are phase 5 of `old-queues/QUEUE-monitoring.md`.

- **Kubernetes 1.37, when its patches settle:** `kubernetes_version` in
  `tofu/proxmox/talos-config.tf` in a commit, then `ansible/talos-k8s-upgrade.yml` (its dry run
  first), then the OpenTofu job. Check first that Talos 1.14 supports 1.37 (its support
  matrix) and the add-ons' ranges: Calico 3.33 is the one that covers 1.37 (3.33.0 tested on
  1.35-1.37). Bump the claude-code image's `KUBECTL_VERSION` once the cluster is on it.

## Cleanup

- **calico-apiserver logs about 860 error lines an hour** (found 2026-10-22, the Logs
  dashboard's error table; cause read 2026-10-22): every one is a `GET
  /apis/projectcalico.org/v3` through the kube-apiserver's aggregation that the caller cut
  short ("Timeout or abort while handling", then three "Unhandled Error" lines for the same
  request: http2 stream closed, Handler timeout), about 350 such requests an hour, most on the
  replica running on a control-plane node. Benign, by Calico's maintainers (projectcalico/calico
  issues 7791 and 13079): aborted long-running requests through an aggregated API are logged
  as errors where a plain resource logs nothing; the Kubernetes fix (kubernetes/kubernetes PR
  131245) was closed unmerged. The APIService is Available, nothing is throttled
  (`calico-apiserver` at 10m CPU, no CFS throttling) and Calico works. Two ways to quiet it,
  neither done: drop those four messages from `calico-system` in Alloy before Loki (the
  monitoring stack's own change), or Calico's native v3 CRDs, which retire the aggregated API
  server (preview since Calico 3.32; this is 3.33.0), a Calico change for the owner to choose.
  Left as is, the lines fill the Logs dashboard's error table and hide real errors there.
- **Delete the Released `local-db` volumes the replica moves left** (2026-10-22): eight replicas
  were rebuilt off the two crowded workers (instances now 8, 7, 8 and 8 per worker), each
  leaving its old PV Released and its folder under `/var/mnt/local-db` on the old worker
  (prowlarr, radarr, n8n, sonarr, manictime, bookshelf, whisparr-v3, authentik). Every
  database is healthy on its new replica, so the old copies are spare. Left: `kubectl delete
  pv` for each, then its folder, as the 2026-10-22 cleanup did. Left, about 6 GB of the
  workers' disks hold stale copies.
- **NetBox's change-log API fails on a `dcim.PortTemplateMapping` change** (found 2026-10-22):
  with the Topology Views patch live (#266, #277; the role-icon rows now render), paging
  `/api/core/object-changes/` answers 500 at the page holding one, "Could not determine
  serializer for dcim.PortTemplateMapping" (NetBox 4.7.2-Docker-5.1.1: the model has no API
  serializer). That's NetBox's own: issue 23280, fixed in NetBox 4.7.3 (2026-10-13; the image
  `ghcr.io/netbox-community/netbox:v4.7.3` is published, checked 2026-10-22); the image now
  builds on it. Left: the redeploy the merge's webhook starts, then every page of
  `/api/core/object-changes/` answering 200.
  `?fields=` without `changed_object` works meanwhile. Left, a script reading the change log
  by API fails until housekeeping drops those rows. Also report the plugin's two faults
  upstream (the text is in #266).
- **Run the baseline on the second Docker VM, for its published ports' answers** (found
  2026-10-20, cause 2026-10-22, Run Command tasks 1102 and 1104): its management address has
  only the rule `from <address> lookup 250`, which a container's answer on a published port
  (kea-standby's API on 8000, the Docker proxy on 9339) never matches: it's routed while its
  source is still the container's, so it leaves by Production's default route and the
  firewall drops it. The first VM has, by hand, an nft table marking management connections
  (`/etc/mgmt-return.nft`, `mgmt-return.service`) and a rule `fwmark 0xfa lookup 250`;
  `tasks/management-routing.yml` now writes the same on a Docker host with
  `management_gateway` set. Left: the owner runs "Setting: Debian Baseline" with
  `target_hosts` the second VM, in check mode, then for real, and a `status-get` from the PC
  answers. Left, a script on the PC that asks the standby gets no answer.

- **`test/media/jackett`** stays as a test-only app for now; not listed in
  `test/kustomization.yaml`.

## Later

- **Every Semaphore task fails while NetBox is down** (found 2026-10-22): Semaphore's inventory is
  NetBox (`ansible/inventory/netbox.yml`), so during NetBox's two-minute rebuild for #290 a
  read-only Run Command failed outright ("exit status 2"). That includes the jobs needed to
  recover NetBox's own host. Consider a fallback: the inventory plugin's cache
  (`cache: true` with a file cache under Semaphore's data), or a static inventory for the
  Docker VMs. Left, a NetBox outage takes Semaphore's every job with it.

- **Vaultwarden's backup puts its archive password on 7z's command line** (found 2026-10-22):
  `ttionya/vaultwarden-backup` 1.27.2's `scripts/backup.sh` runs `7z a ... -p"${ZIP_PASSWORD}"`,
  so for the seconds the nightly archive takes, any process on the first Docker VM that lists
  processes reads `VAULTWARDEN_BACKUP_ZIP_PASSWORD`. Dockhand's backup feeds 7-Zip the
  password on its standard input instead (`docker/dockhand-backup/backup.sh`, `with_password`;
  7-Zip 24.09 reads it there). Report it upstream, or wrap the image the same way. Left, the
  vault's backup password is readable by whatever runs on that host during each backup.
- **Redeploy NetBox for Topology Views 5.0.0** (2026-10-22): it fixes an XSS and a path
  traversal in the role-image settings; it requires NetBox 4.7.0 or later (no maximum), needs
  nothing new, and its serializer file is 4.7.0's byte for byte, so `patch_topology_views.py`
  applies unchanged. Its two migrations (0014, 0015) rework each user's saved view options
  (to a user foreign key and JSON fields, dropping options of deleted users) and run at
  NetBox's start. Left: the merge's webhook rebuilds the image; then NetBox starts with the
  migrations applied (its log), the topology view opens, and a role-icon change still renders
  in `/api/core/object-changes/`.
- **Databasus's two alert emails on 2026-10-21 at 08:03 were refused** ("554 Transaction
  failed" from the cluster's SMTP relay); none failed before or since, and the relay's logs
  from then are gone (its pods restarted). If it recurs, read `smtp-oauth-relay`'s log at the
  time. Left, an alert can be lost without a trace.

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
- **The Docker VM's host still carries DNS, DHCP, the VPN and Vaultwarden together**: a Kea
  HA partner on another host (decision 5) is what's left once both Pi-holes are in use.
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

- [QUEUE-overnight-2026-10-22.md](QUEUE-overnight-2026-10-22.md) (0%): what needs no owner input, worked overnight, with the decisions saved for the owner.
- [QUEUE-autonomy.md](QUEUE-autonomy.md): read first: the owner's standing permissions, the batches I can do on my own, and what waits on the owner.
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
