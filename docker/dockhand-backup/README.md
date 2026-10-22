# Dockhand's backup

What it backs up and when: `compose.yaml`. Archives are in the Garage bucket `docker-backups`,
under `dockhand/`, named `dockhand.<UTC date-time>.7z`, each encrypted with
`DOCKHAND_BACKUP_ZIP_PASSWORD` (Key Vault).

## Check an archive

While Dockhand runs, `docker exec dockhand-backup backup.sh check` downloads the newest archive,
opens it with the password, and checks the database's integrity, its tables, the key's size and
the compose file. It changes nothing.

By hand, on any machine with 7-Zip and the archive: `7z t dockhand.<date>.7z` asks for the
password and ends "Everything is Ok" when it opens; "Wrong password" means it doesn't.

## Restore, on the Docker host

1. Download the archive (from Garage's S3 API with the `docker-backups` key, or the bucket's
   browser) and extract it: `7z x dockhand.<date>.7z` (the password from Key Vault).
2. `docker compose -f /opt/compose/dockhand/compose.yaml down`.
3. Put the files back, owned by Dockhand's user, 1000:
   - `dockhand.db` as `/opt/dockhand/db/dockhand.db`, after deleting that folder's
     `dockhand.db-wal` and `dockhand.db-shm`: they belong to the database being replaced;
   - `.encryption_key` as `/opt/dockhand/.encryption_key`, mode 600;
   - on a new host, `compose.yaml` (and `compose.env` as `.env`) into `/opt/compose/dockhand/`.

   `chown -R 1000:1000 /opt/dockhand`.
4. `docker compose -f /opt/compose/dockhand/compose.yaml up -d`, then open Dockhand: its
   environments, stacks and their variables are back. Each Git stack's files
   (`/opt/dockhand/stacks`, `git-repos`) come back from the repo at its next deploy; they
   aren't in the archive.
