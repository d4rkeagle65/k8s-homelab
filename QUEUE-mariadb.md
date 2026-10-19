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

## To decide (each with a recommendation)

1. **Topology.** Point-in-time recovery (binlogs archived to Garage, since 26.3.0) works
   only with asynchronous replication: a primary and at least 2 replicas, failover by
   `primary.autoFailover`. A single instance gets nightly physical backups only.
   Recommendation: replication with 3 instances for Grimmory, matching what its Postgres
   peers get from CloudNativePG (failover and PITR), if the workers' `local-db` space and
   memory allow; else a single instance and nightly backups, which is what it has today.
2. **MariaDB version.** Grimmory runs 11.8.9 (an LTS); the operator's default is 12.3.
   Recommendation: 11.8 for the move, so it changes one thing; 12.x later as its own update.
3. **Garage bucket and key.** Recommendation: a bucket of its own (`mariadb-backups`) and a
   key with access to it only; the owner makes them and adds the key's two fields to
   Vaultwarden, mapped by a pull request of their own.

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

- `kubernetes/prod/system/mariadb-operator/` (to make): the CRDs and operator HelmReleases,
  their OCI sources (`kubernetes/flux/meta/`, with the other chart sources).
- Each app's `MariaDB`, `Database`, `User`, `Grant` and `PhysicalBackup` in its own folder,
  beside its release (Grimmory's: `prod/apps/grimmory/grimmory/app/`).
- Today's Grimmory database: the `mariadb` controller and `exporter` in its HelmRelease,
  `cronjob-grimmory-db-backup.yaml` (nightly `mariadb-dump` to the NAS, 14 days kept) and
  `secret-grimmory-db.yaml`.

## Checklist

- [ ] Decisions 1 to 3 (the owner)
- [ ] Garage bucket and key; their Vaultwarden fields mapped in `cluster-secrets`
- [ ] The operator: CRDs and operator charts pinned, resource limits, its ServiceMonitor;
      Renovate holds major versions for approval, as for CloudNativePG
- [ ] A trial MariaDB in a test namespace: a physical backup to Garage, a new instance
      bootstrapped from it (`bootstrapFrom`), its tables compared; with replication, a
      point-in-time restore too; then the trial deleted
- [ ] Grimmory: its new `MariaDB` beside the old StatefulSet, loaded from a `mariadb-dump`
      while Grimmory is stopped, row counts compared; Grimmory pointed at it; a backup and
      restore proven; the old StatefulSet, exporter, dump CronJob and its NAS folder retired
      a day later; `QUEUE-restore-tests`'s Grimmory method updated where it's recorded
- [ ] Youtarr: its own `MariaDB`, then its release (its own QUEUE file if it grows)

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
