# Databasus on the Docker host

Nightly backups of the Docker host's own PostgreSQL databases to the Garage bucket
`docker-backups` on the NAS, with failure alerts. The cluster's databases are backed up by
CloudNativePG instead; Vaultwarden's data by the `vaultwarden-backup` container in
`docker/vaultwarden/`.

| Server (stack) | Databases |
|---|---|
| `semaphore-postgres` (`docker/semaphore/`) | `semaphore` (inventories, keys, secrets: encrypted with `SEMAPHORE_ACCESS_KEY_ENCRYPTION`, which isn't in the dump), `tfstate` (OpenTofu's state) |
| `kea-postgres` (`docker/kea/`) | `kea` (the DHCP reservations), `stork` |

Databasus is configured in its web UI and keeps that in `/opt/databasus` on the host, so this
file is the record of what's set there.

## Once, before the first deploy

1. **The Garage bucket and key**, as root on the NAS. The `garage` image has no shell, so
   Dockhand's console can't open one; `docker exec` runs the binary directly:

   ```sh
   docker exec garage /garage bucket create docker-backups
   ```
   ```sh
   docker exec garage /garage key create docker-backups
   ```
   ```sh
   docker exec garage /garage bucket allow --read --write docker-backups --key docker-backups
   ```

   The key's ID and secret go into Dockhand only (here and in the vaultwarden stack), with a
   copy in Vaultwarden.
2. **The read-only role.** Set `DATABASUS_DB_PASSWORD` (a new random password; the same value
   in both stacks is fine) on the **semaphore** and **kea** stacks and redeploy them. Each
   `databasus-role-init` log ends `databasus role: login true, reads all data true`.
3. **The folder:** `install -d -m 700 /opt/databasus` as root on the host.
4. **The networks:** `docker network ls` on the host. `SEMAPHORE_NETWORK` and `KEA_NETWORK`
   are the two stacks' networks (the ones their `*-postgres` containers are on: `docker
   inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' kea-postgres`).

## Variables (the stack's environment in Dockhand)

| Variable | Value |
|---|---|
| `DATABASUS_BIND_ADDRESS` | the host's management address |
| `DATABASUS_URL` | `https://databasus.<domain>` |
| `SEMAPHORE_NETWORK` | the semaphore stack's network |
| `KEA_NETWORK` | the kea stack's network |
| `DATABASUS_AGENT_ID` | the verification agent's ID (below) |
| `DATABASUS_AGENT_TOKEN` | secret: its token (below) |

## In the web UI, after the first deploy

Open `https://databasus.<domain>` (allow ~2 minutes for the first start) and create the first
account: the first one administers the instance. Keep its password in Vaultwarden. Then:

- **Storage:** S3, endpoint `http://<the NAS's management address>:3900`, region `garage`,
  bucket `docker-backups`, path prefix `databasus/`, path-style addressing, the
  `docker-backups` key.
- **Databases:** four PostgreSQL 17 entries, user `databasus` with `DATABASUS_DB_PASSWORD`,
  logical backups:
  `semaphore-postgres:5432` / `semaphore` and `tfstate`; `kea-postgres:5432` / `kea` and `stork`.
- **Schedule:** daily at 03:30 UTC, after the cluster's database backups and the 01:00
  Proxmox backup. **Retention:** 30 days.
- **Notifier:** email through the cluster's SMTP relay (as Vaultwarden sends), failures only.
- **Run one backup of each by hand** and check it lands in the bucket.

## Restore verification

`databasus-verification-agent` restores backups into throwaway PostgreSQL containers and
reports each table's row count. It has the host's Docker socket (root on the host; see the
note in `compose.yaml`).

1. In the UI, **Settings → Verification agents → Create verification agent** (e.g.
   `docker-vm`). The dialog shows its **ID** and its **token**, once: put them in
   `DATABASUS_AGENT_ID` and `DATABASUS_AGENT_TOKEN` (secret), keep a copy of the token in
   Vaultwarden, and redeploy. Ignore the dialog's install commands; the stack runs the agent.
2. The agent shows as online in that list within a minute. Its log:
   `docker logs databasus-verification-agent`.
3. For each database, turn verification on (its **Verifications** tab): after every backup, or
   on a schedule such as weekly, with the email notifier for failures.
4. Queue one check on each now and read the report: every table with its row count.

A lost token is replaced with **Rotate token** on the agent's row, then the new one in
Dockhand.

## Updating

Change the image tag in `compose.yaml` and redeploy.
