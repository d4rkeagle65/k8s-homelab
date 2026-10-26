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

- **Delete two orphan iSCSI LUNs on the Synology** (found 2026-10-22): `k8s-pvc-b71edef3-...`
  from the 2026-10-14 `iscsi-move-test7` claim, and `k8s-pvc-9afd3fcf-...`, Zammad's first
  Elasticsearch volume (`old-queues/QUEUE-zammad.md`). Neither has a PV any more; delete each LUN and its
  target in DSM's SAN Manager. Left, they hold NAS space nothing reads (both still listed by
  the Synology's SNMP, hex-encoded, 2026-10-22).
- **Dockhand's Key Vault principal down to "Key Vault Secrets User"** (from
  `old-queues/QUEUE-azure-keyvault.md`): its provider only lists and reads secrets
  (`azure-kv.ts`), yet "Dockhand Key Vault Access" holds "Key Vault Secrets Officer"; the role
  change run 2026-10-21 didn't take (Claude Code's, run alongside, did), and it still held only
  Officer on 2026-10-22. Left, anything that reaches Dockhand's credential can write every secret.
- **`nas-block` off the hosts and the NAS** (from `old-queues/QUEUE-talos.md`), in this order:
  the Proxmox storage removed (`pvesm remove nas-block`, cluster-wide); on each host, its iSCSI
  session logged out, its node record deleted (`iscsiadm`) and its volume group removed; the
  LUNs (`metrics-1` to `-5`) and targets deleted in DSM; then `ansible/tasks/proxmox-nas-block.yml`,
  its include in `proxmox-setting.yml`, the `nas_block_*` variables in Semaphore and the Drives
  dashboard's LUN-writes panel. Read 2026-10-22: no PV or class uses it; the storage is still
  defined on all five hosts (0 used) and the five LUNs are still on the Synology.
- **The power-cut failover test** (from `old-queues/QUEUE-talos.md`): one host at a time, its
  power cut in a quiet window, everything back on the others; for H4, the Docker VM and the DC
  VM back on H5 within about three minutes, then on H4 again once it's up. Check the room first:
  without H5 the other workers hold about 43 GiB against about 48 GiB of memory requests
  (2026-10-22), so some pods would wait for it.
- **Wipe H5's old `HDD` volume group** (from `old-queues/QUEUE-talos.md`, Phase 0): the 2 TB
  HDD still holds it; wipe it once its two old VM disks (111, 117) are confirmed backed up.
  Unverified 2026-10-22 (no Proxmox storage lists it, which doesn't show the group is gone).

Decisions that unblock the work below; each gives a recommendation. Numbers stay as
assigned (items below refer to them); 1 to 10 are all done (7, Docker's upgrade from
Semaphore, with the first VM's reboot on 2026-10-22).

## Next

- **The lab's Proxmox hosts onto 9** (2026-10-25): the two test hosts run 8.3.5, production
  9.2.21. `ansible/proxmoxlab-update-major.yml` upgrades them in place, one at a time, the group
  `proxmox_lab` only, from Semaphore's template 35 "Update: Proxmox Lab (8 to 9)" (made
  2026-10-26; survey `target_hosts`, `stop_guests`). Dry run 1328 passed; real run 1329 (2026-10-26) took
  cotedeprox01 to 9 (pve8to9: 0 failures, 0 warnings; sources on trixie; upgrade installed),
  then lost it at the reboot: no SSH or web UI 20 minutes on, so the run stopped before
  cotedeprox02 (still 8.3.5, untouched). Left: the owner at deprox01's console (where boot
  stopped; if at a login, `ip -br link` against /etc/network/interfaces, as a renamed NIC
  under the new kernel is the usual cause, `proxmox-network-interface-pinning generate` the
  fix), then the playbook changed to match (pin names first, or wait through the reboot)
  before a run on cotedeprox02. Nothing on them needs keeping.
- **Kept in the cluster** (2026-10-24): `QUEUE-kept.md` (decisions, the owner's Key Vault and
  Garage steps first, then two pull requests in order).
- **The IoT devices in Home Assistant** (2026-10-23): a firewall rule lets the 4 workers reach
  the IoT prefix on the devices' ports (HA is hostNetwork, so devices are added by IP, without
  discovery). `scripts/ha_iot_setup.py` adds each local device from NetBox by its setup flow
  and puts devices in rooms from their NetBox Location (this branch's pull request moves rooms
  there, adds --attended for the TVs' setups, and lets one device's timeout not stop the run;
  Semaphore task 1275 stopped on the Fire Stick's ADB prompt, creating nothing). NetBox has a
  Location per room, and all 49 IoT devices are in theirs (read back). Left:
  merge it; a second Semaphore template with --apply --attended beside template 32; then a run of each.
  HA's Nursery area stays for Baby Buddy. Tabled by the owner on 2026-10-24. The dryer and
  washer Tapo plugs refuse the TP-Link login (invalid_auth, tasks 1276-1280, also after a
  restart of both), though the group's values are copied from the owner's working login: next,
  add the dryer plug in the UI by hand; if that works, make the script trim stray whitespace
  and quotes from the login variables; if not, toggle Third-Party Compatibility with the plugs
  online. The "livcor" Kasa device is off, so each run spends 90 s on it and ends in error. The cloud integrations (Tuya, VeSync, Whirlpool;
  HACS' Alexa Media Player, Govee Cloud Integration, Hatch, eufy) take the owner's logins in
  the UI; a later run places their devices that carry a MAC. Then pin the HACS add-ons in the
  repo (offered, its own pull request). The son's-room ceiling fan doesn't work: look into it.
  Revoke the token in ~/.ha-token when this is done.
- **The IoT devices' exact models and DNS names** (2026-10-23): the NetBox sync's "iot
  devices:" step (#340) made all 39 on its first run (task 1244, read back: 39 devices, each
  wifi0 with its MAC and address), types and roles from `scripts/netbox_seed_iot.yaml`'s rules.
  Left: the exact models the rules can't tell, which the Kasa devices report to Home Assistant:
  set by hand in NetBox the "kalihall" and "kalistairs" switches to HS210, "kalidining" to
  HS220, "kalilivcou" and "kalilivend" to HS103 plugs ("kalilivcor" is unknown, no lease);
  and the Echo generations, by NETBOX_SEED_IOT_MODELS in the "Netbox Sync" variable group or
  by hand in NetBox (the sync sets a type only when it creates the device); and the zone the
  IoT hostnames go in, for their DNS names (the owner wants them there: the IoT prefix is in
  NetBox DNS's default view, so a DNS name becomes a Pi-hole record). Left, every IoT
  switch reads HS200, and the hostnames sit in the descriptions. Kea holds 49 IoT
  reservations and NetBox has a device for each (sync task 1254, read back). The two Hatch
  Rest 2nd Gens are still on Production: their reservations wait for the owner to move them
  to IoT.
- **The Immich import from OneDrive** (2026-10-21): Cloud Sync on the NAS brings the owner's
  OneDrive pictures down into the media share's `oneDrive` folder, and
  `kubernetes/prod/apps/immich/immich-import/`'s CronJob uploads what Immich lacks, nightly at
  04:30 (the Immich CLI, as the media apps' user, 1028:100, to the in-cluster Service, at 4Gi:
  the CLI holds each file in memory while it uploads it). The bulk upload (2026-10-22) accounted
  for all 5,071 new files; a nightly run hashes the whole folder, 7m53s for 9,264 files
  (2026-10-22, none new), and Immich's job queues are empty. Left: the owner reviews Immich's
  duplicates (Utilities, Review duplicates: 734 assets in 211 groups on 2026-10-22), the
  re-encoded copies the hash couldn't match.
- **Dockhand's Restart of `kea` leaves `stork-agent` stopped** (found 2026-10-22): after compose
  has started the stack, Dockhand restarts its containers once more, and `stork-agent`, which
  shares `kea-dhcp4`'s network and PID namespaces, dies with it and fails to come back ("cannot
  join network namespace of a non running container"; Dockhand logs "1 orphan container(s)
  failed to restart"). Redeploy kea instead, which runs `compose up`. Left: report it to Dockhand
  (github.com/Finsys/dockhand) with that log line; until then, a Restart leaves Stork blind to
  Kea while DHCP runs on.
- **Report two Grimmory 3.5.0 bugs upstream** (found 2026-10-22, worked around here):
  Refresh Metadata's task (`LibraryRescanTask.validatePermissions`) checks
  `permissionAccessTaskManager` without the admin fallback the other tasks have (fixed here by
  listing every permission on the admin group, #332); and it copies a file's "ASIN" unchecked
  into the 20-character `book_metadata.asin`, where Calibre's 36-character UUIDs (MOBI EXTH
  113) fail the save, and because the whole library is one transaction
  (`LibraryRescanHelper.handleRescanOptions`) that one book undoes every book's refresh. 27
  books had their ASIN locked through Grimmory's field lock (`asinLocked`), and the refresh
  then completed (2026-10-23, 179 books, 47 s). Posting the issues needs the owner's go-ahead.
  Left as is, a new book whose file carries such an ID fails the next refresh again: lock its
  ASIN the same way.
- **Read the Grimmory relink job's first real relink** (on since 2026-10-22): the
  `grimmory-relink` CronJob (`kubernetes/prod/apps/grimmory/grimmory/app/`, hourly at :50)
  points a book at its file again when Bookshelf moves it, before Grimmory's 01:00 scan would
  delete the book with its shelves, progress and notes. Proved before it was turned on: its
  hash equals Grimmory's stored one on all 214 files, none shared, and a file renamed by hand
  on the share gave one "would move" line with the right book and new path. Left: after
  Bookshelf's next rename or move pass, read the job's log ("relinked", then the read-back)
  and check the book kept its shelves. Not covered, and reported only: an upgrade (Bookshelf
  replacing a book's file with a different one) is new content, so the scan still replaces
  that book.
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
  issue 602's area). Until then the job fails each night on it, and KubeJobFailed tickets it
  (the hand-started job's ticket was cleared by deleting it, 2026-10-22).
  **Pace any repair by hand to Hardcover's daily budget** (2026-10-18: about 2,000 repair
  lookups in an afternoon spent the token's whole day, and every uncached Bookshelf lookup
  failed for hours): read `rg_gql_ratelimit_remaining{bucket="daily"}` before and during a
  batch and stop well short of 0; one work every few seconds; never while Bookshelf is
  identifying new files, or several large authors at once. The budget (Hardcover's response
  headers, 2026-10-19): 5,000 lookups a day, reset at 00:00 UTC, and 60 a minute with bursts
  of 10; a re-fetched work costs about one, so keep a day's repairs to about 1,000. The
  HardcoverDailyBudget alerts (homelab.bookshelf) ticket both the low and the spent budget.
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
- **Retire `bitwarden-cli`: merge #205 from 2026-10-26** (from
  `old-queues/QUEUE-azure-keyvault.md`): a week on Key Vault by then, and `cluster-secrets` is
  the only ExternalSecret, reading `azure-keyvault`, so nothing reads the three `bitwarden-*`
  stores. Read back after it: the `bitwarden-cli` Flux Kustomization and its objects gone,
  `cluster-secrets` Ready and `SecretSynced`, every Kustomization Ready. Then the owner deletes
  the CLI's hand-applied `bitwarden-cli` Secret in `external-secrets`,
  `kubernetes/.local/bitwarden-cli.env`, and the `vaultwarden-direct.<domain>` record, which
  only the CLI used. Vaultwarden stays the password manager for people.
- **Claude Code's Key Vault sign-in, checked from inside its container** (from
  `old-queues/QUEUE-azure-keyvault.md`): deployed 2026-10-21 (#206), Dockhand resolving 3/3
  references, but Entra has no sign-in by `k8s-homelab-claude-code` since. Left:
  `docker/claude-code/README.md`'s dry run from inside the container, `already the same` for
  each name.
- **Kea: the Stork agent on the standby** (from `old-queues/QUEUE-kea-ha.md`):
  `docker/kea-standby/` runs Kea alone; the agent's image (`homelab/kea-tools`, built on the
  host by the primary's stack) has to be built on the second Docker VM first. Until then Stork
  sees only the primary.

## Cleanup

- **No check catches a `raw` script Ansible can't split** (found 2026-10-25, task 1302): Ansible
  splits a `raw` task's free-form text on quotes, comments included, so one bare apostrophe
  (`tasks/nas-bastion-keys.yml`, "ls's") failed the Synology run at loading, before any
  change. `--syntax-check` doesn't load `include_tasks` files, and the suite has no Ansible. A
  test that parses every `raw` task with `ansible.parsing.mod_args.ModuleArgsParser` (after
  `init_plugin_loader()`) when Ansible is installed, in `tests/test-ansible-hosts.py`, would
  have caught it; without one the next stray quote fails a run on the host.

- **calico-apiserver logs about 860 to 1,250 error lines an hour** (found 2026-10-22, the Logs
  dashboard's error table; cause read 2026-10-22; 1,250 by that afternoon): every one is a `GET
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
- **Report Topology Views' two faults upstream** (found 2026-10-22; the text is in #266): NetBox's
  change-log API failed on the plugin's role-icon rows, fixed here by `patch_topology_views.py`
  in NetBox's image (#266, #277), and on `dcim.PortTemplateMapping`, fixed by NetBox 4.7.3
  (#290); every page of `/api/core/object-changes/` answers 200 since 2026-10-22. Nothing is
  filed on netbox-community/netbox-topology-views yet. Left unreported, every plugin release
  needs the patch checked again.

- **Drop `kea` from `kea-postgres` from 2026-10-24** (from `old-queues/QUEUE-kea-ha.md`): both
  Keas read `kea-db` since the 2026-10-17 cutover, and the old copy stays a week. Then the
  database goes from the first Docker VM's `kea-postgres`, `docker/kea/initdb/00-databases.sh`
  stops creating it, and Databasus stops dumping it (it still did on 2026-10-21).

## Later

- **Key Vault per app, with workload identity, so `cluster-secrets` can retire** (the owner,
  2026-10-24): today one service principal reads the whole vault, and the `azure-keyvault`
  store serves `flux-system` and namespaces labelled `homelab.local/keyvault: allowed`
  (opt-in, the first being the Tailscale exporter's). The end state: each app's
  ServiceAccount federated to an Azure identity holding "Key Vault Secrets User" on only its
  own secrets, a namespaced `SecretStore` per app, nothing secret stored in the cluster. It
  needs the Talos service-account issuer's OIDC discovery document and keys at a public URL
  Azure can fetch. Then move each app off `cluster-secrets` and retire it. A queue file of
  its own when started.
- **Vaultwarden's backup puts its archive password on 7z's command line** (found 2026-10-22):
  `ttionya/vaultwarden-backup` 1.27.2's `scripts/backup.sh` runs `7z a ... -p"${ZIP_PASSWORD}"`,
  so for the seconds the nightly archive takes, any process on the first Docker VM that lists
  processes reads `VAULTWARDEN_BACKUP_ZIP_PASSWORD`. Dockhand's backup feeds 7-Zip the
  password on its standard input instead (`docker/dockhand-backup/backup.sh`, `with_password`;
  7-Zip 24.09 reads it there). Report it upstream, or wrap the image the same way. Left, the
  vault's backup password is readable by whatever runs on that host during each backup.
- **Databasus's two alert emails on 2026-10-21 at 08:03 were refused** ("554 Transaction
  failed" from the cluster's SMTP relay): the relay's log (Loki, namespace `smtp-relay`) has
  Microsoft Graph answering 429 `ApplicationThrottled` at 08:03:52 and 08:04:51 UTC; none failed
  before or since. Left: if it recurs, a retry of a throttled send, in the relay or the sender.
  Left as is, an alert in a burst can be lost.

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
    `remove_old_kernels: true` on the Proxmox inventory and run the upgrade template. Read
    2026-10-22: `6.14.11-4` is already gone; four hosts have `6.14.11-9`, `7.0.14-20` and
    `7.0.14-22` (running), the fifth only 7.0 kernels, and the daily check lists nothing to
    remove. Every run lists what it would remove.
  - Don't run `zpool upgrade` unless a new ZFS feature is needed.

- **Ansible for the hosts outside the cluster**, so their hand-made setup
  can be rebuilt from git instead of from notes:
  - **Docker VM:** netplan (its three network legs, routing tables and rule
    priorities), the nft scripts and their systemd units for the DMZ marks and the
    `DOCKER-USER` rules for Tailscale, sysctls, Dockhand's own compose file and the
    certificate-sync cron. Docker (`dockerhost-deploy.yml`) and the management return path
    (`tasks/management-routing.yml`) are done.
  - **Proxmox hosts:** `ansible/proxmox-setting.yml` holds the ZFS ARC cap, the ACME
    certificates and the NAS block storage (all applied), `debian-baseline.yml` the common
    packages, and `proxmox-update.yml` the kernel cleanup. Still to add there: repositories.
  - **Pi-hole:** `ansible/pihole-setting.yml` sets what nebula-sync can't copy
    (`misc.dnsmasq_lines`) on both, from Semaphore. Everything else (local
    records such as `vaultwarden-direct`, CNAMEs, lists, `bogusPriv`) lives on
    Pi-hole 1 and is copied to Pi-hole 2 by nebula-sync, so losing both would
    lose it: a scheduled Teleporter export to Garage would cover that.
  - **Named logins instead of root:** the owner's account with sudo is on every machine
    (`debian-baseline.yml`, run 2026-10-10). Once it's proven everywhere: remove the owner's
    key from root's `authorized_keys` (on the Proxmox hosts the shared
    `/etc/pve/priv/authorized_keys`), and set `sshd` to `PasswordAuthentication no` and
    `PermitRootLogin prohibit-password` (never `no`: the Proxmox hosts and Semaphore log in
    as root by key), from a playbook that first checks the named login works.
  - **Scheduled check-mode runs in Semaphore:** weekly since 2026-10-22 (schedules 14-17, the
    Setting templates with `dry_run`; pending updates daily already). A failed run is a Zammad
    ticket (the bridge's `/semaphore` route, the project's Alert on, 2026-10-22). Left: a run
    that finds drift still succeeds, its `changed=` count only in the log; consider failing a
    check run that would change something.

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
- **Stork's agent exports only some subnets' lease figures after a restart** (found 2026-10-22):
  since the kea stack's redeploy recreated `stork-agent` (2.5.1) that afternoon,
  `kea_dhcp4_addresses_assigned_total` exists for subnets 227 and 237 only, not 107 (38 leases)
  or 250, though Kea's own `statistic-get-all` has all four; after the next restart (16:25 EDT)
  it was 237 and 250, so each restart drops a different set. Homepage and the overview
  dashboard read Kea's own counts from `kea-ha-exporter` instead (`docker/kea/README.md`), so
  nothing here reads Stork's figures now. Left: an upstream report to Stork, before anything is
  built on its metrics again.
- **Immich's storage-template migration fails for some files** (found 2026-10-22: 19 failures
  beside 42 successes in 12 hours of its log, after the OneDrive import): read why before the
  duplicate review. Left, those files stay where they were uploaded, outside the template's
  layout.

- **Lease names into DNS** (from `old-queues/QUEUE-kea-ha.md`): Kea's DDNS to the Pi-holes, or
  a sync; its own decision when it's taken up. Until then a device is reachable by name only
  if NetBox has it.
- **Immich's PostgreSQL, when an Immich release calls for it** (from
  `old-queues/QUEUE-updates.md`): `immich-postgres` runs `cloudnative-vectorchord:16-1.1.1`
  (Debian 12), which is supported; it moves when an Immich release's notes ask for a newer
  PostgreSQL or VectorChord, as the others did (`old-queues/QUEUE-postgres18.md`), on a
  VectorChord image of the new major. Immich 3.3.5's notes, read 2026-10-22, ask for neither.
- **Kubernetes 1.36.5 -> 1.37.1** (Renovate's #9, held since 2026-10-15; from
  `old-queues/QUEUE-updates.md`): until Flux (2.9.6 lists 1.34-1.36), cert-manager (1.21.2,
  1.33-1.36; 1.22 due about 2026-11), External Secrets (2.13.0 lists 1.36) and CloudNativePG
  (1.30.1, 1.34-1.36) support it; Talos 1.14 and Calico 3.33 already do, and no removed API is
  in use. Then `kubernetes_version` in `tofu/proxmox/talos-config.tf` in a commit,
  `ansible/kubernetes-update.yml` (its dry run first), the OpenTofu job, and afterwards the
  claude-code image's `KUBECTL_VERSION`.
- **PyYAML's pin in the sync jobs, once it has Python 3.15 wheels**: Python 3.15.1 is in
  (#14 and #300, merged 2026-10-23 before PyYAML had them; read back 2026-10-25: the Grimmory,
  media and Organizr sync jobs each succeeded on `python:3.15.1-alpine`, Baby Buddy's MCP pod
  Ready on 3.15.1). PyYAML 6.0.3, still its newest, has no cp315 wheels, so the four build it
  from source at every start. When a release has them (`pip download --only-binary=:all:
  --python-version 3.15`, for `musllinux_1_2_x86_64` and the `manylinux` tags), move the
  three jobs' `pyyaml==6.0.3` pin to it, since Renovate can't see a pin inside a command.
  Left, each run spends its first minute building PyYAML.

## Working queue files

- [QUEUE-bastion.md](QUEUE-bastion.md) (just started): Termix, a web-based bastion into every host, its hosts from NetBox.
- [QUEUE-tailscale.md](QUEUE-tailscale.md) (just started): the tailnet's policy, DNS and alerts from `tofu/tailscale` and NetBox.
- [QUEUE-network-monitoring.md](QUEUE-network-monitoring.md) (just started): SNMPv3, syslog and alerts for the core switch and the firewall.
- [QUEUE-resource-limits.md](QUEUE-resource-limits.md) (about 85%): requests and limits on every container, in batches.

The percentages are estimates as of 2026-10-22, from each file's checklist weighed by the
size of what's left; update one when its file changes. A long job gets a `QUEUE-<task>.md` at
the repo root; `CLAUDE.md` says when. A
finished one moves to `old-queues/`, whose README indexes what each settled and which checks
came back negative.
