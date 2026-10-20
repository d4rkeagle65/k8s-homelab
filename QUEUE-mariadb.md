# MariaDB through an operator, as CloudNativePG does Postgres

The ask (the owner, 2026-10-19): run MariaDB with an operator, the way CloudNativePG runs
Postgres, so a MariaDB database is declared in git and backed up without a script. Grimmory
moves onto it, retiring its dump CronJob (`grimmory-db-backup`); Youtarr, the YouTube
downloader the owner wants, is the second database on it (it supports MariaDB or MySQL only).

## Settled

- **mariadb-operator** (`github.com/mariadb-operator/mariadb-operator`): 26.10.1 on
  2026-10-19 (skip 26.10.0, per its release notes), Kubernetes 1.31 or later, MariaDB 10.6
  or later. Two Helm charts, both OCI: `oci://ghcr.io/mariadb-operator/charts/mariadb-operator-crds`
  and `.../mariadb-operator`, so the CRDs upgrade on their own schedule.
- **One MariaDB per app**, as each app has its own CloudNativePG cluster: an app's upgrade,
  restore or failure touches no other app's data. Its database, user and grants are
  `Database`, `User` and `Grant` resources, not an init script.
- **Backups to Garage**, as the Postgres ones are: `PhysicalBackup` (mariadb-backup; the
  operator's recommended kind) on a schedule with `maxRetention`. Metrics through the
  operator's mysqld-exporter and its ServiceMonitor, which replace Grimmory's own exporter.
- **Youtarr waits for this**, rather than getting a MariaDB of its own first and moving later.
- **Topology** (the owner, 2026-10-19): Grimmory on asynchronous replication, 3 instances
  (failover, and point-in-time recovery from binlogs archived to Garage, which needs that
  topology); Youtarr on one instance with nightly physical backups (its media can be fetched
  again). About +1 GiB of memory in all with the operator (measured 2026-10-19: Grimmory's
  MariaDB peaks at 230 MiB; the workers had 12 GiB unrequested).
- **MariaDB 11.8** for Grimmory's move (what it runs now, an LTS); 12.x later as its own update.

## Rules

- **A backup counts once a restore from it has been read back**, table by table, as
  `old-queues/QUEUE-restore-tests.md` did for Postgres. A completed `PhysicalBackup` alone
  proves nothing.
- **Keep `kustomize.toolkit.fluxcd.io/prune: disabled`** on every `MariaDB` resource and
  its Secrets (`kubernetes/CLAUDE.md`), so Flux never deletes a database.
- **A new variable reaches the cluster first** (root `CLAUDE.md`): Garage's key fields are
  mapped and merged before anything uses them.
- **The old database stays a day** after an app moves, as the PostgreSQL 18 moves did, and
  is deleted by hand once the new one has served that day.

## Where things live

- `kubernetes/prod/services/mariadb-system/mariadb-operator/` (beside CloudNativePG's
  `cnpg-system`): the CRDs and operator HelmReleases; their chart source is the OCI
  HelmRepository `kubernetes/flux/meta/repositories/mariadb-operator.yaml`.
- Each app's `MariaDB`, `Database`, `User`, `Grant` and `PhysicalBackup` in its own folder,
  beside its release (Grimmory's: `prod/apps/grimmory/grimmory/app/`).
- Today's Grimmory database: the `mariadb` controller and `exporter` in its HelmRelease,
  `cronjob-grimmory-db-backup.yaml` (nightly `mariadb-dump` to the NAS, 14 days kept) and
  `secret-grimmory-db.yaml`.

## Checklist

- [x] Garage bucket `mariadb-backups` and a key Garage created, allowed on it alone (the owner,
      2026-10-20; `docker/garage/README.md`, More buckets); `MARIADB_BACKUP_S3_ACCESS_KEY_ID`
      and `_SECRET_ACCESS_KEY` in Key Vault and `cluster-secrets`, the endpoint
      `CNPG_BACKUP_S3_ENDPOINT`'s. Read back: the key writes, reads and deletes there and is
      refused on `cnpg-backups`
- [x] The operator (2026-10-20): `prod/services/mariadb-system/mariadb-operator/`, the CRDs
      and operator charts at 26.10.1 from an OCI HelmRepository (the CRDs release never
      pruned), `clusterName` the cluster's domain. Read back: 12 CRDs, the operator, webhook
      and cert-controller ready, the webhook registered, all three scraped. Memory at start:
      84, 60 and 16 MiB (the cert-controller over its 32Mi request: raise it when tuning)
- [x] A trial (2026-10-20, namespace `mariadb-trial`, applied by hand and deleted after): a
      3-instance replicated MariaDB 11.8.9 on `local-db`, one per worker; replicas matched the
      primary's row counts and checksums, and a switchover lost nothing. A `PhysicalBackup` to
      `mariadb-backups` completed, and a new instance bootstrapped from it (`bootstrapFrom.
      backupRef`) matched exactly: counts, `CHECKSUM TABLE`, the foreign key; it took writes.
      **Point-in-time recovery didn't work:** the agent logged "Starting binary log
      archiver" and archived nothing, with no `status.pointInTimeRecovery`, no event and no
      operator log line, both when added to a running MariaDB and when configured from its
      creation (base backup present, empty prefix, binlogs flushed). Its objects, namespace and
      released PVs were removed; the PVs' folders may remain on the workers (`local-db`
      retains; QUEUE.md's entry on retired databases' files)
- [x] Grimmory without point-in-time recovery for now (the owner, 2026-10-20: option a):
      replicated, 3 instances (failover; nightly physical backups); PITR retried on a later
      operator release
- [x] `MARIADB_BACKUP_S3_HOST` (2026-10-20): Garage's address as `host:port`, since
      `storage.s3.endpoint` takes no scheme and `CNPG_BACKUP_S3_ENDPOINT` has one. Read back:
      equal to the endpoint without its scheme
- [ ] Grimmory, in steps:
      1. (2026-10-20) `grimmory-db` beside the old `grimmory-mariadb` StatefulSet
         (`app/mariadb.yaml`): 3 instances of 11.8.9 on `local-db` (5Gi), its `myCnf` keeping
         `innodb_snapshot_isolation=OFF`, its `Database`, `User` and `Grant` as the old one's
         (cleanupPolicy Skip), and a nightly `PhysicalBackup` to `mariadb-backups/grimmory`
         (14 days). Grimmory's Kustomization waits for `mariadb-operator`. Left: the merge, then the instances, grants, the first
         backup and a restore from it read back
      2. (done) The move (2026-10-20, Grimmory stopped from 01:55Z): its release suspended and scaled to
         0, a `mariadb-dump` of the old database loaded into `grimmory-db-0` (the primary). All
         73 tables' row counts matched (1,766 rows), and the new primary's and a replica's
         checksums matched each other; `book`'s `CHECKSUM TABLE` differed from the old one's
         while its definition, row format and a hash of every row's values matched (the old
         table's storage, not its data). The `grimmory` login was raised to 30 connections
         first (the operator's default is 10; Grimmory's pool holds 10). `DATABASE_URL` points at
         `grimmory-db-primary`; the release resumed, Grimmory back at 02:32Z (37 minutes down),
         its connections on `grimmory-db` and none on the old database, healthy, 178 books.
         A backup taken with the data, restored into a scratch instance, matched the live
         database: all 73 tables' row counts and `book`'s hash over every value; the scratch
         instance, its backup and PV were removed
      3. From 2026-10-21: the old StatefulSet, its exporter and ServiceMonitor, the dump CronJob and
         its PVC removed, and the NAS dump folder; the old data PVC's PV deleted once released
- [ ] Youtarr's `MariaDB`: in `QUEUE-youtarr.md`, with its release

## Resuming

The first unticked box is next. A phase is done when its state is read back from the
cluster (and, for a database, a restore from its backup compared).

## Done when

Every box is ticked, no MariaDB in the cluster is backed up by a script, and this file moves
to `old-queues/` with a row in its README.

## Checked and negative

- **Youtarr on Postgres:** no. Its docs support "an existing MariaDB or MySQL instance"
  only, and its only database driver is `mysql2` (`package.json`, v1.89.0).
- **CloudNativePG** covers Postgres only.
- **Other operators:** Percona's (XtraDB Cluster or Group Replication), Oracle's MySQL
  Operator (InnoDB Cluster) and Moco are MySQL; KubeBlocks is a multi-database platform,
  far more than two small databases need.
