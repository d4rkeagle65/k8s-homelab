# Overnight, 2026-10-22: what needs no owner input

**The ask (the owner, 2026-10-22 00:10 UTC):** "work on what you can out of the queues without
my input. Make another overnight queue for those things. And save any decisions for me and
continue what you can."

## Settled

- Each piece ends as its own branch and pull request (`CLAUDE.md`); queue-only commits go to
  `main`. Nothing is merged, applied, deployed or deleted; no Semaphore template that changes
  a host runs; no NetBox or Key Vault write. Read-only Run Commands and dry runs are fine.
- Each piece is worked in its own git worktree, never the shared `netbox` one: two agents in
  one worktree switched each other's branches on 2026-10-21.
- Before calling a PR open, its state is read (`gh pr view`).
- A question only the owner can answer goes under "Decisions for the owner" and the work
  moves on; it never blocks the rest.

## Checklist

| Piece | `QUEUE.md` entry | Result |
|---|---|---|
| A | `test-script-health`'s standard-library check only knows the platform it runs on | PR #267 (stacked on #264: merge that first) |
| B | `test-harness` reads red in a Claude desktop worktree | PR #264 |
| C | `keyvault-secrets.ps1` shows Dockhand's secrets as `Synced` `no` | PR #263 |
| D | Nothing owns the Pi-holes' timezone | PR #265 (the zone is each container's Proxmox `timezone` option; needs `lxc_timezone` in the Proxmox hosts' variables) |
| E | NetBox's change-log API fails on Topology Views' role icons | PR #266 (upstream 5.0.0 has the same serializer; a patch at build) |
| F | `docker/claude-code/README.md`'s "Updating" | PR #271 (README only; leftovers of retired stacks added to `QUEUE.md`) |
| G | kea-standby's API doesn't answer from the owner's PC (read-only diagnosis) | PR #274 (the second VM lacks the first's mark-based management return; the baseline writes it) |
| H | NetBox has none of MetalLB's addresses (`netbox_seed.py` reads the LoadBalancers) | PR #268 (needs `K8S_API_URL`, `K8S_TOKEN`, `K8S_CA_B64` on the NetBox sync template's environment before merging) |
| J | Databasus's failures (asked 00:15) | Backups all succeed (5 nightly, 00:00); the 15 verifications fail: the agent holds the deleted Docker socket. PR #272 fixes the post-upgrade check that missed it |
| I | Morning reads: the Proxmox backup (01:00 UTC), rreading-glasses repair dry run (07:15 UTC), Immich import dry run (08:30 UTC) | Backup clean (every host OK by 03:12 UTC, no NFS stall, no CRM change); the two dry runs are read after they run |


### Round 2 (asked 2026-10-22 ~04:00 UTC: "go through all of the queue files ... do as much as you can", then bug scans)

| Piece | Entry (file) | Result |
|---|---|---|
| K | calico-apiserver logs about 860 error lines an hour (`QUEUE.md`) | No PR: benign. Aborted aggregated-API requests logged as errors, expected upstream (calico 7791, 13079); options recorded in `QUEUE.md` (`ba673b8`) |
| L | `netbox-topology-views` 5.0.0 carries security fixes (`QUEUE.md`) | PR #286 (5.0.0 supports NetBox >= 4.7.0; the serializer patch applies unchanged, retested) |
| M | Bookshelf, its database and rreading-glasses sized (`QUEUE-resource-limits.md`) | PR #288 (7.4 days of data; merging restarts bookshelf-db by switchover, rreading-glasses-db in place) |
| N | A LimitRange's defaults reach a Job's pods only when they run: a check (`QUEUE.md`) | PR #289 (`scripts/limitrange_jobs.py`; live: 23 namespaces, 20 Job templates, nothing flagged) |
| O | Kea's database CA, `verify-ca` prepared (`QUEUE-kea-ha.md`) | PR #291 (cert-manager CA and server certificate for kea-db; clients stay `require` until the host steps in `QUEUE-kea-ha.md`) |
| P | Dockhand's notifications into tickets: the compose labels (`QUEUE-zammad.md`) | PR #287 (all eight one-shot services already labelled; a test keeps it so) |
| Q | Read-only rechecks: NetBox `PortTemplateMapping` upstream, Kubernetes 1.37, Python 3.15 (`QUEUE.md`, `QUEUE-updates.md`) | `ba673b8`: Kubernetes 1.37 and Python 3.15 still held; NetBox's `PortTemplateMapping` fixed in 4.7.3: PR #290 |
| R | Move database replicas off the crowded workers (`QUEUE.md`) | All eight done (prowlarr, then with the owner's go-ahead radarr, n8n, sonarr, manictime, bookshelf, whisparr-v3, authentik; 31-62 s each, every cluster healthy): instances 8, 7, 8, 8 per worker, from 13, 3, 4, 11. Eight Released `local-db` PVs and folders left on the old workers |
| S | Bug scans, several passes, after the pieces above | Five passes: (1) a code review of everything merged since 2026-10-21, (2) a repo-wide sweep for the bug classes that bit before, (3) the live systems, (4) each finding tried and fixed only once reproduced, (5) a review of the fixes. Fixes: #292 (NetBox sync retires stale load-balancer records), #293 (KubeJobFailed only for a CronJob's latest run), #294 (primary spreading only after a drain, never skipping clean-up; pass 5 caught its Talos fact on the wrong host, fixed), #297 (Flux held whenever the whole group is named), #298 (Immich import deadline, Grimmory relink NULL path and errors, repair report on stop, Dockhand backup's listing and 7z password off argv), #299 (timezone inside the container and locked containers; the routing script clears its own rules). Refuted: two. Live systems: healthy. The six merge cleanly in any order; #286 and #298 both edit `QUEUE.md` |

### Merged and validated (the owner's go-ahead, 2026-10-22 ~13:00 UTC), one at a time

#302 (docs), #287 (its suite passes on main), #289 (its script, live: exit 0, nothing flagged),
#293 (only the repo's KubeJobFailed rule loaded; the four stale alerts cleared to 0), #292 (28
tests pass on main; the nightly sync is its live check), #297 (dry run 1148 with `proxmox`
held Flux), #294 (dry run 1149 drained and planned the spread, 4/4/4/4; 1150 with nothing to
drain skipped it; clean-up ran in both), #299 (dry runs 1151: only test container 199 would
change zone; 1152: the new routing script installs at the next real run), #298 (CronJobs carry
the fixes; the rebuilt Dockhand backup wrote and `backup.sh check` restored a new archive),
#288 (every pod on its new resources; both databases healthy), #291 (both Certificates Ready,
kea-db serving the new CA's certificate without a restart, both Keas without a database
error), #286 (NetBox with Topology Views 5.0.0, its migrations 0014 and 0015 applied, the
role-icon rows render; rebased onto #298 with `QUEUE.md` resolved), #290 (NetBox 4.7.3: all
16 change-log pages answer 200). #295 skipped, as the owner asked.

## Decisions for the owner

- **`TALOS_SECRETS`:** the owner regenerates it from a control plane (`talos/README.md`) and
  keeps it in a Vaultwarden login of its own, not `cluster-secrets`; Semaphore's
  `TF_VAR_talos_secrets` (OpenTofu) stays as it is.
- ~~**Rotate `grimmory-db`'s login password?**~~ Left as is (the owner, 2026-10-22): the
  password is 24+ characters and mixed, so the hash printed into the 2026-10-21 session's
  output (not git) can't practically be cracked; rotating would cost a Grimmory restart.
- **#258 (database placement rule) restarts every database once:** when to merge; then a
  go-ahead to move about eight replicas, one at a time.
- ~~**Pod Security:** 14 namespaces enforce `restricted`, 21 `baseline`.~~ Split (the owner,
  2026-10-22): elastic-system, mesh-mini and zammad to `restricted` (they lacked only seccomp
  and dropped capabilities), PR #303; the other 18 stay `baseline` by decision (their images
  would have to run as non-root, app by app). Left in `QUEUE.md`: the merge and read-back.
- **#260 (Dockhand backup):** `DOCKHAND-BACKUP-ZIP-PASSWORD` in Key Vault, then the stack in
  Dockhand, and its ID for the webhook.
- ~~**The old Grimmory dump folder on the Synology**~~: deleted by the owner, 2026-10-22 (15 dumps, 2.1 MB, of the retired database; read back gone, `grimmory-data` untouched).
- **The `evacuate` override in NetBox's Proxmox config context**: drop it (a NetBox write).
- **DMZ clients' second Pi-hole address**, by hand on each.
- **The Pi-holes' DMZ addresses in git history:** rewrite history, or leave them.
- **Scheduled weekly check-mode runs in Semaphore** need a template schedule (Manager role).
- ~~Restart `databasus-verification-agent`~~: done 2026-10-22 01:53 (approved; it reaches Docker
  again). Left: a verification from Databasus's UI, or tonight's at 00:00 UTC.
- ~~The first Docker VM's root disk was 99% full~~: 41% (18 GB free) at 01:53, cleaned by the owner. Was: (30 of 32 GB, 492 MB free, 2026-10-22): what
  reclaims without touching a running container: `docker builder prune -af` (6.7 GB build
  cache), `docker image prune -af` (8.4 GB of images no container uses), and Dockhand's scanner
  caches `docker volume rm dockhand-grype-db dockhand-trivy-db` (5 GB, downloaded again at the
  next scan). Longer term the disk grows in OpenTofu. Full, the next image pull or backup
  temp file fails (Databasus's health checks did on 2026-10-21 08:01).

- **The eight Released `local-db` PVs and their folders** from the replica moves (R): delete
  them (the PVs by `kubectl delete pv`, the folders as in the earlier cleanup), each database
  being healthy on its rebuilt replica.
- ~~**Pod Security for the remaining 21 namespaces**~~: decided above (three raised, #303; 18
  stay `baseline`).
- **The workers' old vzdump archives on `tm-nfs`**, and the retired Dockhand stacks' leftovers
  on VM1: delete them.

Pieces that end in a decision add it here.

## Resuming

Read the checklist; a piece with a PR number is done (check its state); one without is
redone from its `QUEUE.md` entry in a fresh worktree from `origin/main`. Each piece stands
alone.

## Done when

Every row has a result (a PR, a finding in `QUEUE.md`, or a decision above), and the morning
report has gone to the owner. Then this file moves to `old-queues/` with a README row, and the
decisions still open move into `QUEUE.md`.
