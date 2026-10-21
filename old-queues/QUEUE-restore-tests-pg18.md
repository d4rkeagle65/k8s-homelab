# Restore-test the PostgreSQL 18 clusters' backups

**The ask:** prove each CloudNativePG cluster's backup in Garage restores (`QUEUE.md`,
"Restore-test the PostgreSQL 18 clusters' backups"; the owner, 2026-10-21). Since the
retired clusters' backups were deleted, these are the only copies. The method is
`old-queues/QUEUE-restore-tests.md`'s, which proved the old clusters' on 2026-10-09.

## Settled

- **Where:** a one-instance Cluster `<cluster>-rt` in the source's own namespace, so it uses
  the existing `ObjectStore` (`garage`) and its credentials. Applied by hand with `kubectl`,
  never through git or Flux, labelled `restore-test=true`, on `nfs-eph-rwo` at the source's
  size, so the workers' `local-db` disks aren't touched.
- **How it restores:** `bootstrap.recovery` from an `externalClusters` entry with the Barman
  Cloud plugin (`barmanObjectName: garage`, `serverName` the source's), the source's initdb
  `database` and `owner`, the same `imageName` and `shared_preload_libraries`. No
  `spec.plugins`, so the copy never archives into the source's path.
- **The archive is made current first:** `SELECT pg_switch_wal()` on the primary, then 90 s,
  since `archive_timeout` is an hour (`kubernetes/CLAUDE.md`).
- **What counts as proven:** exact `count(*)` of every base table in every non-template
  database, the copy against the live cluster (its replica; the primary for the one-instance
  `rreading-glasses-db`), both read with `default_transaction_read_only`. A difference is
  explained only by the live application's writes after the copy's last replayed
  transaction ("pass", with the tables named); anything else fails and gets its own entry in
  `QUEUE.md`.
- **Tooling:** one script does a cluster end to end (switch, apply, wait up to 30 min, count,
  compare, delete): the steps above, run from a session with `kubectl` to the cluster.
- **Cleanup:** the copy is deleted after its comparison, pass or fail (its PVC goes with it).

## Checklist

| Order | Namespace | Cluster | Result |
|---|---|---|---|
| 1 | babybuddy | babybuddy-db | match: 35 tables, 18,740 rows (136 s) |
| 2 | mealie | mealie-db | match: 69 tables, 3,537 rows (136 s) |
| 3 | linkwarden | linkwarden-db | match: 18 tables, 1,330 rows (90 s) |
| 4 | n8n | n8n-db | match: 157 tables, 1,285 rows (136 s) |
| 5 | kea | kea-db | match: 64 tables, 111 rows (91 s) |
| 6 | authentik | authentik-db | pass: 230 tables, 357,120 rows (363 s); live ahead in `authentik_tasks_task` (+5) and `authentik_tasks_tasklog` (+68), its background tasks |
| 7 | manictime | manictime-postgres | pass: 2 databases, 52 tables, 510,463 rows (242 s); live ahead by 2 to 15 rows in six `cl_ch_`/`cl_co_` tables and three `ar_` report tables, the clients' activity uploads |
| 8 | immich | immich-postgres | match: 72 tables, 299,508 rows (257 s) |
| 9 | zammad | zammad-db | pass: 131 tables, 254,740 rows (317 s); only `sessions` (253 against 335), which churns: 104 of its 106 rows were created in the last 30 minutes when checked |
| 10 | media | bookshelf-db | pass: 3 databases, 44 tables, 29,205 rows (332 s); `Logs` +6, `Commands` +2 |
| 11 | media | lidarr-db | pass: 2 databases, 43 tables, 145,003 rows (364 s); `Commands` +5 |
| 12 | media | prowlarr-db | pass: 2 databases, 23 tables, 25,092 rows (332 s); `Logs` +6, `History` +6 |
| 13 | media | radarr-db | pass: 2 databases, 44 tables, 91,804 rows (333 s); `Commands` 6 fewer live, the app's housekeeping deleting finished commands |
| 14 | media | sonarr-db | pass: 2 databases, 41 tables, 95,989 rows (347 s); `Logs` +3, `Commands` 5 fewer live (housekeeping) |
| 15 | media | whisparr-v3-db | pass: 2 databases, 44 tables, 3,362 rows (347 s); `Commands` 5 fewer live (housekeeping) |
| 16 | media | rreading-glasses-db | match: 1 table, 73,302 rows (91 s), against its primary |

All 16 restored on 2026-10-21: 7 exact matches, 9 passes, no failure. Every difference was
in a table the application writes continuously (task queues, logs, sessions, command
history), by a handful of rows.

## Resuming

`kubectl get clusters.postgresql.cnpg.io -A -l restore-test=true` lists any copy left behind:
delete it, then carry on at the first row without a result. Each row stands alone, so a
half-finished pass is still valid.

## Done when

Every row has a result, any failure has its own entry in `QUEUE.md`, and no `-rt` cluster is
left. Then this file moves to `old-queues/` with a row in its README, and `QUEUE.md`'s entry
goes.
