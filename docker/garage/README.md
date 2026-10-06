# Garage on the NAS

S3-compatible object storage for CloudNativePG's backups (the Barman Cloud plugin needs an S3
API, not an NFS share). One Garage node in Docker on the NAS, deployed by Dockhand through the
NAS's Hawser agent from this folder. Its data stays on the NAS, outside the cluster and the
Docker host.

| | |
|---|---|
| `compose.yaml`, `garage.toml`, `.env` | here, in git: nothing site-specific |
| `<GARAGE_DIR>/garage.toml` | on the NAS: a copy of `garage.toml` (the container reads it) |
| `<GARAGE_DIR>/meta/`, `<GARAGE_DIR>/data/` | on the NAS: Garage's metadata and the stored objects |

Docker runs on the NAS, so the stack mounts its config from a path there, not from this
folder: after changing `garage.toml` here, copy it to the NAS again and redeploy.

## Before the first deploy (once, on the NAS over SSH)

1. **A folder for Garage** on a NAS volume, with `meta/` and `data/` in it, for example
   `/Volume1/garage` (TOS's volume paths). Put `meta/` on an SSD if the NAS has one.

   ```sh
   mkdir -p /Volume1/garage/meta /Volume1/garage/data
   ```

2. **The config:** copy this folder's `garage.toml` to `/Volume1/garage/garage.toml`.
3. **Three random values** for the stack's secrets: one `openssl rand -hex 32` (the RPC
   secret, which must be hex) and two `openssl rand -base64 32` (the admin and metrics
   tokens). Keep them in Vaultwarden.

## Variables (the stack's environment in Dockhand)

Mark every one **secret**, as for the other stacks; `.env` lists them blank for Populate.

| Variable | Value |
|---|---|
| `GARAGE_BIND_ADDRESS` | the NAS's address the cluster reaches it on (the S3 API, port 3900) |
| `GARAGE_DIR` | the folder from step 1, e.g. `/Volume1/garage` |
| `GARAGE_RPC_SECRET` | secret: the hex value |
| `GARAGE_ADMIN_TOKEN`, `GARAGE_METRICS_TOKEN` | secret: the two base64 values |

## After the first deploy (once, on the NAS)

Garage starts with no storage assigned. These commands run Garage's CLI inside the container.

1. **Find the node's ID** (the first column):

   ```sh
   docker exec garage /garage status
   ```

2. **Give the node its storage and apply it.** `-c` is how much of the NAS Garage may use
   (a target for placing data, not a hard quota):

   ```sh
   docker exec garage /garage layout assign -z nas -c 500G <node ID>
   ```

   ```sh
   docker exec garage /garage layout apply --version 1
   ```

3. **The bucket and its key:**

   ```sh
   docker exec garage /garage bucket create cnpg-backups
   ```

   ```sh
   docker exec garage /garage key create cnpg-backups
   ```

   The second prints the key's ID and its secret. Put both straight into Vaultwarden as
   `CNPG_BACKUP_S3_ACCESS_KEY_ID` and `CNPG_BACKUP_S3_SECRET_ACCESS_KEY` in the
   `cluster-secrets` item; the cluster reads them from there once its backup configuration
   is in git.

4. **Let the key use the bucket:**

   ```sh
   docker exec garage /garage bucket allow --read --write --owner cnpg-backups --key cnpg-backups
   ```

5. **Check it:** `docker exec garage /garage bucket info cnpg-backups` lists the key as
   allowed, and from a machine that reaches the NAS, `curl -s http://<NAS address>:3900`
   answers with an S3 error document (`AccessDenied`): the API is up.

## Updating

Change the image tag in `compose.yaml` and redeploy. Read Garage's release notes first: a
major version can change the metadata format, and its automatic metadata snapshots
(`metadata_auto_snapshot_interval`) are what to fall back on.
