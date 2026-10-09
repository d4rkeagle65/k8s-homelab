# Restore-test every CloudNativePG backup

**The ask:** prove each cluster database's Barman backup restores, one database at a time,
without the owner stepping in. Mealie's was proven when backups were set up; these are the
other eleven.

## Settled

- **Where:** a one-instance Cluster `<cluster>-rt` in the source's own namespace, so it uses
  the existing `ObjectStore` and its S3 credentials (no Secret is copied). It is applied by
  hand with `kubectl`, never through git or Flux, and carries the label `restore-test=true`.
- **How it restores:** `bootstrap.recovery` from an `externalClusters` entry with the Barman
  Cloud plugin (`barmanObjectName` = the source's store, `serverName` = the source cluster),
  replaying WAL to the end of the archive. The copy has no `spec.plugins`, so it never archives
  into the source's path. Same `imageName` and `shared_preload_libraries` as the source
  (immich needs `vchord.so`); storage `nfs-eph-rwo` at the source's size, so the workers'
  `local-db` space is not touched.
- **What counts as proven:** exact `count(*)` of every base table in every non-template
  database, the copy against the live cluster (a standby when there is one), both read with
  `default_transaction_read_only`. A difference is explained only by writes after the copy's
  last replayed transaction (the archive lags the live database by up to `archive_timeout`,
  5 minutes); anything else is a failure.
- **Cleanup:** the copy is deleted after the comparison (its PVC goes with it).

## Checklist

| Order | Namespace | Cluster | Result |
|---|---|---|---|
| 1 | n8n | n8n-postgres | 2026-10-09: match: 149 tables, 1,264 rows (90 s) |
| 2 | immich | immich-postgres | 2026-10-09: match: 67 tables, 232,537 rows (90 s) |
| 3 | authentik | authentik-postgres | 2026-10-09: pass: 216 tables, 326,578 rows; `authentik_core_session` 2 fewer in the copy (sessions after the archive point) (271 s) |
| 4 | babybuddy | babybuddy-postgres | 2026-10-09: match: 35 tables, 18,728 rows (61 s) |
| 5 | manictime | manictime-pg | 2026-10-09: pass: 44 tables, 253,889 rows; six activity tables 4 to 9 rows fewer in the copy (the client logging since the archive point) (151 s) |
| 6 | media | bookshelf-postgres | 2026-10-09: pass: 44 tables, 4,391 rows; `Commands` 6 more in the copy (pruned live since) (241 s) |
| 7 | media | lidarr-postgres | 2026-10-09: match: 43 tables, 147,798 rows, both databases (241 s) |
| 8 | media | prowlarr-postgres | 2026-10-09: match: 23 tables, 9,385 rows, both databases (241 s) |
| 9 | media | radarr-postgres | 2026-10-09: pass: 44 tables, 89,323 rows; `Commands` 6 fewer in the copy (241 s) |
| 10 | media | sonarr-postgres | 2026-10-09: pass: 41 tables, 90,388 rows; `Commands` 4 fewer in the copy (241 s) |
| 11 | media | whisparr-postgres | 2026-10-09: match: 39 tables, 14,944 rows, both databases (241 s) |

Times are from apply to a healthy copy. "Match" is every count equal; "pass" is equal except
tables the live application wrote after the archive's last segment.

**To re-run one later**, as was done here: build the copy from the source with `jq`
(`instances: 1`, the source's `imageName` and `shared_preload_libraries`, storage
`nfs-eph-rwo` at the source's size, `bootstrap.recovery` with `source: origin` and the
source's initdb `database` and `owner`, `externalClusters: [{name: origin, plugin: {name:
barman-cloud.cloudnative-pg.io, parameters: {barmanObjectName: garage, serverName: <source>}}}]`),
wait for `Cluster in healthy state`, then count with `query_to_xml(format('select count(*) as c
from %I.%I', ...))` over `information_schema.tables` in each database, through `kubectl exec
... psql` on each side.

## Resuming

`kubectl get clusters.postgresql.cnpg.io -A -l restore-test=true` lists any copy left behind:
delete it, then carry on at the first row without a result. Each row stands alone, so a
half-finished pass is still valid.

## Done when

Every row has a result, any failure has its own entry in `QUEUE.md`, and no `-rt` cluster is
left. Then this file moves to `old-queues/`.
