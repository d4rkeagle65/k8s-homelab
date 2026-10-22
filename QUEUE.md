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

## Cleanup

- **The NetBox sync's Kubernetes credentials** (2026-10-22): `scripts/netbox_seed.py` now reads
  the cluster's LoadBalancer Services and MetalLB pools and records each address (role VIP,
  the Services as its description) and each pool's range, and stops without
  `K8S_API_URL`, `K8S_TOKEN` and `K8S_CA_B64`. Left: the owner adds those three to the
  "NetBox: Sync from Proxmox and Kea" template's environment as environment variables (secret:
  the values the Ansible jobs' `k8s_api_url`, `k8s_token` and `k8s_ca_b64` hold), before the
  pull request is merged; then the next run's log (it applies: its arguments hold `--apply`)
  shows five LoadBalancer addresses and the `production` range under "load balancers:", read
  back in NetBox. Left, the nightly sync fails from the merge on.

- **The old Grimmory database's dump folder on the Synology** (2026-10-21, the owner's to
  delete): `grimmory-grimmory-db-backup-pvc-5df26534-…` under the NFS share's `k8sData`, the
  nightly `mariadb-dump`s of the database #225 retired (its PV is gone; the data is superseded
  by `grimmory-db`'s physical backups). Left, 14 days of stale dumps take NAS space.

- **Move database replicas off the two crowded workers** (found 2026-10-21): of 31
  CloudNativePG instances, one worker runs 13 and another 11, the other two 3 and 4, so losing
  either busy one fails over about six databases. Every Cluster now keeps its two instances apart
  and prefers the workers with the fewest instances (`scripts/tests/test_structure.py`,
  `test_databases_spread`), and the primaries piled up by a full drain are spread back at the
  end of the four drain jobs (`ansible/tasks/cnpg-spread-primaries.yml`), but a `local-db`
  volume holds each existing instance to its node, so the rule only places new ones. Left: move
  about eight replicas, one at a time, each with the owner's go-ahead: delete the replica's pod
  and PVC so CloudNativePG rebuilds it from its primary on the worker the rule picks, wait for
  "Cluster in healthy state", then the next. Each leaves a Released `local-db` PV and its folder
  on the old worker to clean up. Left as is, the next worker lost takes six primaries with it.

- **NetBox's change-log API fails on a `dcim.PortTemplateMapping` change** (found 2026-10-22):
  with the Topology Views patch live (#266, #277; the role-icon rows now render), paging
  `/api/core/object-changes/` answers 500 at the page holding one, "Could not determine
  serializer for dcim.PortTemplateMapping" (NetBox 4.7.2-Docker-5.1.1: the model has no API
  serializer). That's NetBox's own; look for it upstream and take the release that fixes it.
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

- **Leftovers on the first Docker VM from retired Dockhand stacks** (found 2026-10-22, Run
  Command tasks 1091-1098, read only): the image `homelab/claude-code:k8s-1.33.3-flux-2.9.5-helm-4.2.2`
  (the tag scheme before `local`), Dockhand's clones `Local/k8s-homelab` (at a 2026-10-05
  commit) and `Local/neetbox` under `/opt/dockhand/git-repos`, and `stacks/Local/k8s-homelab`,
  which no running stack uses. Check Dockhand lists no stack by those names, then remove them
  by hand. Left, they take disk space and read as live stacks.
- **`test/media/jackett`** stays as a test-only app for now; not listed in
  `test/kustomization.yaml`.

## Later

- **`netbox-topology-views` 5.0.0 carries security fixes** (found 2026-10-22): an XSS and a
  path traversal in the role-image settings, released 2026-10-19; `docker/netbox/` pins 4.7.0.
  Check 5.0.0 supports NetBox 4.7, then bump it (`patch_topology_views.py` from #266 refuses
  5.0.0's class only if its text changed; its serializer is unchanged). Left, the plugin's
  settings pages keep those holes.
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
- **Set the containers' timezone from `proxmox-host.yml`** (2026-10-22): each container's
  `timezone` option is what holds, since Proxmox writes it into the container at every start
  (both Pi-holes read `America/Detroit` inside and in their configs, Run Command tasks 1086-1088;
  the stopped test container 199 has none). `tasks/proxmox-lxc-timezone.yml` sets every
  container's option to `lxc_timezone`, and inside a running one now. Left: the owner adds
  `lxc_timezone` (the zone `TIMEZONE` holds) to the Proxmox hosts' variable group, then runs
  "Setting: Proxmox Host" in check mode, then for real. Left unset, a rebuilt Pi-hole comes back
  with whatever zone its template had.
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
- [QUEUE-monitoring.md](QUEUE-monitoring.md) (about 80%): Prometheus, Loki and Grafana, in eight phases.
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
