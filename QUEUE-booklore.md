# Booklore, the e-book reader

**The ask:** an e-book reader like Calibre over the books Bookshelf collects, in Organizr, Homepage
and Grafana, configured from git with the Authentik login.

## Settled

- **Booklore** (owner's choice over Calibre-Web Automated and Kavita), v2.4.0 through
  app-template in `prod/apps/booklore/`, running as the media user (`MEDIA_PUID`), the library
  mounted read-write from the NAS's `/volume1/media/media/books` at `/books`.
- **MariaDB** (Booklore supports no other database) in the same release, on `local-db`, with a
  `mysqld-exporter` sidecar for Prometheus and a nightly `mariadb-dump` to the NAS
  (`booklore-db-backup`, 14 days).
- **Configured from git** like Organizr: `sync/booklore.yaml`, applied by the `booklore-sync` Job
  (first-run admin, library, Authentik login, group mappings, Homepage's read-only user), read back.
- **Login:** Authentik (the `booklore` blueprint); Booklore Admins are admins, Booklore Users read,
  download and sync. Booklore keeps its local login form for the local admin.

## Checklist

- [ ] The variables reach the cluster, then the rest is pushed; the blueprint re-applied once the
      worker restarts (`kubernetes/CLAUDE.md`)
- [ ] MariaDB and Booklore start; the Job's first run passes
- [ ] Login through Authentik as a Booklore Admin; the Books library scans
- [ ] Homepage's widget shows counts; the Organizr tab opens; Grafana's MySQL dashboard (Apps
      folder) shows the database
- [ ] The first nightly dump lands and `gzip -t` passes; a restore test into a scratch database
- [ ] Reader apps over OPDS (each user's OPDS login in Booklore)

## Resuming

`kubectl -n booklore logs job/booklore-sync` shows the last run; the first unticked box is next.

## Done when

Every box is ticked and this file moves to `old-queues/`.
