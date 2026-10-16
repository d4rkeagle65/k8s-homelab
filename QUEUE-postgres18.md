# Every database onto PostgreSQL 18 on Debian trixie

**The ask (2026-10-15):** the PostgreSQL majors in `QUEUE-updates.md`. The owner chose a new
cluster per database over CloudNativePG's in-place upgrade, which can't leave Debian 11. ManicTime
and Whisparr went first; their entries there have how it went.

## Why a new cluster

Every cluster but Immich's runs a plain `ghcr.io/cloudnative-pg/postgresql:<n>` tag: Debian 11
(bullseye) `system` images. Those tags are deprecated and no longer built, so they get no patch
releases and Renovate offers none, and Debian 11's LTS ended 2026-08. CloudNativePG's in-place
major upgrade needs the same Debian on both sides. Once on trixie, later majors can go in place.

## Rules (each database the same way)

- **Name:** `<app>-db`, beside `<app>-postgres`, in the same folder (`db.yaml`, or
  `cluster-<app>-db.yaml` in a `.promote` folder, then `generate`). The old cluster's settings,
  `imageName: ghcr.io/cloudnative-pg/postgresql:18.6-standard-trixie` (Renovate moves the minor),
  `kustomize.toolkit.fluxcd.io/prune: disabled`, and a nightly `ScheduledBackup` beside the old
  one's. Its archive path is its own name, so it never meets the old one's.
- **Before:** a base backup, waited on until `last_archived_wal` reaches its `endWal`
  (`kubernetes/CLAUDE.md`), then a restore test (`old-queues/QUEUE-restore-tests.md`).
- **Find where the app reads its connection** and change that in the cutover commit: a file on
  the app's volume isn't changed by its manifests (ManicTime's wasn't).
- **Copy with the app stopped:** suspend its HelmRelease (or Flux Kustomization) and scale it to
  0; no connection left on the old databases; then per database `kubectl exec <old primary> --
  pg_dump -Fc -d <db> | kubectl exec -i <new primary> -- pg_restore -d <db> --no-owner
  --role=<owner> --exit-on-error`, then `ANALYZE`. As `postgres` on each side: no password moves.
- **An idle database's backup can end exactly on a segment boundary**, leaving its `endWal`
  empty and never archived: `CHECKPOINT`, then `pg_switch_wal()`, before waiting on it
  (Mealie's and n8n's).
- **A Django app (BabyBuddy, NetBox) holds no connection while idle**, so "connections on
  the new cluster" reads zero: prove it by `inet_server_addr()` from the app's own shell, or by
  the new database's `xact_commit` rising across requests while the old one's doesn't.
- **Applied means:** every table's exact row count, every table and index, and every sequence's
  position identical old against new, all owned by the app's role; after the cutover, the app's
  connections all on the new cluster and none on the old, and the app answering.
- **The old cluster stays a day** as the way back, then is removed from git and deleted by the
  owner with its claims.

## Checklist

Smallest risk first; Authentik last, since every forward-auth login waits on it.

| Order | Cluster | Size | The app reads its connection from |
|---|---|---|---|
| 1 | `media/prowlarr-postgres` | 47 MB | env in its HelmRelease: **done 2026-10-15**, `prowlarr-db`, 60 s down |
| 2 | `media/bookshelf-postgres` | 69 MB | env in its HelmRelease (`READARR__POSTGRES__*`): **done 2026-10-15**, `bookshelf-db`, 67 s down |
| 3 | `media/radarr-postgres` | 79 MB | env in its HelmRelease: **done 2026-10-15**, `radarr-db`, 64 s down |
| 4 | `media/sonarr-postgres` | 105 MB | env in its HelmRelease: **done 2026-10-15**, `sonarr-db`, 65 s down |
| 5 | `media/lidarr-postgres` | 142 MB | env in its HelmRelease: **done 2026-10-15**, `lidarr-db`, 63 s down |
| 6 | `mealie/mealie-postgres` | 37 MB | env in `deployment.yaml`: **done 2026-10-15**, `mealie-db`, 75 s down |
| 7 | `linkwarden/linkwarden-postgres` | 34 MB | env in its HelmRelease: **done 2026-10-15**, `linkwarden-db`, 67 s down |
| 8 | `n8n/n8n-postgres` | 38 MB | env in its HelmRelease: **done 2026-10-15**, `n8n-db`, 101 s down |
| 9 | `babybuddy/babybuddy-postgres` | 35 MB | `configmap-babybuddy-env.yaml` and its Deployment: **done 2026-10-15**, `babybuddy-db`, 81 s down |
| 10 | `netbox/netbox-postgres` | 49 MB | its HelmRelease: **done 2026-10-15**, `netbox-db`, 130 s down |
| 11 | `authentik/authentik-postgres` | 175 MB | `values.yaml` (captured release; `generate`): **done 2026-10-15**, `authentik-db`, 108 s down |
| 12 | `immich/immich-postgres` | | Not due: its image (vectorchord `16-1.1.1`, Debian 12) is supported, unlike the bullseye ones the others left; it moves when an Immich release's notes call for a newer PostgreSQL |

Each row is ticked with its date, the downtime and anything that differed, and gets a
"retire `<app>-postgres`" line until its old cluster is gone.

## Retire the old clusters

From about 2026-10-17, once each app has run a day on 18: remove `postgres.yaml` and its
`kustomization.yaml` line (`<app>/app/`), and `scheduledbackup-<app>-postgres.yaml` with its line
(`media-backup/app/`); the owner then deletes the Cluster (kept from pruning) and its claims
`<app>-postgres-1`/`-2`.

- [ ] Prowlarr, Bookshelf, Radarr, Sonarr, Lidarr (moved 2026-10-15)
- [ ] Mealie, Linkwarden, n8n (`n8n/app/`): `postgres.yaml` and `scheduledbackup.yaml` with
      their lines in `app/kustomization.yaml` (moved 2026-10-15). NetBox's went with its
      namespace when NetBox moved to the Docker VM
- [ ] Authentik: `cluster-authentik-postgres.yaml` (`authentik-extras`, `.promote`; `generate`
      after) and `scheduledbackup-authentik-postgres.yaml` (`authentik-backup/`) (moved 2026-10-15)
- [ ] BabyBuddy: `cluster-babybuddy-postgres.yaml` (`.promote`; `generate` after) and
      `scheduledbackup-babybuddy-postgres.yaml` (`babybuddy-backup/`) (moved 2026-10-15) The retirements from ManicTime and
Whisparr are in `QUEUE-updates.md`.

## Resuming

`kubectl get clusters.postgresql.cnpg.io -A -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,IMAGE:.spec.imageName`
shows which are on 18. A `-rt` copy (`-l restore-test=true`) left behind is deleted. A database
half done is safe either way: until the cutover commit is pushed, the app is on the old cluster,
and a new cluster can be emptied with `pg_restore --clean --if-exists` and copied into again.

## Done when

Every row is ticked, every old cluster retired, and `QUEUE-updates.md`'s item ticked; then this
file moves to `old-queues/`.
