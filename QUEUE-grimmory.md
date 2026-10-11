# Grimmory, the e-book reader

**The ask:** an e-book reader like Calibre over the books Bookshelf collects, in Organizr, Homepage
and Grafana, configured from git with the Authentik login.

## Settled

- **Grimmory** v3.5.0 through app-template in `prod/apps/grimmory/`, running as the media user
  (`MEDIA_PUID`), the library mounted from the NAS's `/volume1/media/media/books` at `/books`,
  with `DISK_TYPE=NETWORK` (no deletes, moves or renames on the NFS library). It replaced
  Booklore on 2026-10-11, which the owner had chosen over Calibre-Web Automated and Kavita:
  Booklore is in maintenance mode; its official successor, BookOrbit, doesn't support network
  storage and has one developer; Grimmory is the community's fork, with Booklore's API and
  database, maintained by a team, and with a mode for network storage.
- **MariaDB** (Grimmory supports no other database) in the same release, on `local-db`, with a
  `mysqld-exporter` sidecar for Prometheus and a nightly `mariadb-dump` to the NAS
  (`grimmory-db-backup`, 14 days).
- **Configured from git** like Organizr: `sync/grimmory.yaml`, applied by the `grimmory-sync` Job
  (first-run admin, library, Authentik login, group mappings, Homepage's read-only user), read back.
- **Login:** Authentik (the `grimmory` blueprint); Grimmory Admins are admins, Grimmory Users read,
  download and sync. Grimmory keeps its local login form for the local admin.

## Checklist

- [ ] The variables (copies of Booklore's) reach the cluster, then the rest is pushed; the blueprint
      re-applied once the worker restarts (`kubernetes/CLAUDE.md`)
- [ ] MariaDB and Grimmory start; the sync Job's first run passes
- [ ] The library scans as one book per file (folders hold whole series; one book per folder
      failed 39 of them under Booklore, which found 255 books per file)
- [ ] Login through Authentik as a Grimmory Admin (the groups were Booklore's, renamed)
- [ ] Homepage's widget shows counts; the Organizr tab opens; Grafana's MySQL dashboard (Apps
      folder) shows the database
- [x] Booklore removed (2026-10-11): its namespace, its three volumes and their data (on a
      worker and the NAS), its Authentik application and provider, and the BOOKLORE_* fields
- [ ] The first nightly dump lands and `gzip -t` passes; a restore test into a scratch database
- [ ] Reader apps over OPDS (each user's OPDS login in Grimmory)

## Resuming

`kubectl -n grimmory logs job/grimmory-sync` shows the last run; the first unticked box is next.

## Done when

Every box is ticked and this file moves to `old-queues/`.
