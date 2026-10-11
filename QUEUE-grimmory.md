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

- [x] The variables (copies of Booklore's) reached the cluster, then the rest; the blueprint
      re-applied once the worker restarted
- [x] MariaDB and Grimmory start; the sync Job's first run passed (6 settings, the library, both
      group mappings, Homepage's user, all read back)
- [x] The library scans as one book per file: 158 books from 246 files (a title's EPUB, MOBI,
      AZW3 and PDF are one book), no failures
- [x] Homepage's widget logs in; the Organizr tab is there; Grafana's MySQL dashboard (Apps folder)
      has data (`mysql_up` 1)
- [x] Booklore removed (2026-10-11): its namespace, its three volumes and their data (on a
      worker and the NAS), its Authentik application and provider, and the BOOKLORE_* fields
- [x] The nightly dump, run by hand: `gzip -t` passes, and restored into a scratch database it
      matches the live one (73 tables, 0 differences, 158 books)
- [ ] Login through Authentik as a Grimmory Admin. Grimmory's own test of the provider failed:
      it refuses outbound connections to private addresses, and Authentik is one;
      `OIDC_ALLOW_UNSAFE_HOSTS` lifts that for its OIDC calls only. Rerun its test (Settings,
      OIDC), then log in
- [ ] Reader apps over OPDS: each user makes an OPDS login in Grimmory's settings, and the app
      points at `https://grimmory.<domain>/api/v1/opds` (private networks only, so at home or
      over Tailscale). The endpoint is on and asks for a login (checked)

## Resuming

`kubectl -n grimmory logs job/grimmory-sync` shows the last run; the first unticked box is next.

## Done when

Every box is ticked and this file moves to `old-queues/`.
