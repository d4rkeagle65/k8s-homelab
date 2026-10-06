# Garage on the NAS

S3-compatible object storage for CloudNativePG's backups (the Barman Cloud plugin needs an S3
API, not an NFS share). One Garage node in Docker on the NAS, deployed by Dockhand through the
NAS's Hawser agent from this folder. Its data stays on the NAS, outside the cluster and the
Docker host.

| | |
|---|---|
| `compose.yaml`, `.env` | here, in git: nothing site-specific; Garage's config is inline in `compose.yaml` |
| `<GARAGE_DIR>/meta/`, `<GARAGE_DIR>/data/` | on the NAS: Garage's metadata and the stored objects |

Docker runs on the NAS, so the stack can't mount a file from this folder: its host paths are
paths on the NAS. Garage's config is inline instead (`configs:` with `content:`), which Compose
2.23 and later copies into the container through Docker's API. If the container exits with
"no config file", Dockhand's Compose is older: put the config in `<GARAGE_DIR>/garage.toml` and
mount it at `/etc/garage.toml` instead.

## Before the first deploy (once, on the NAS over SSH)

1. **A folder for Garage** on a NAS volume, with `meta/` and `data/` in it, for example
   `/Volume1/garage` (TOS's volume paths). Put `meta/` on an SSD if the NAS has one.

   ```sh
   mkdir -p /Volume1/garage/meta /Volume1/garage/data
   ```

2. **Five random values** for the stack's secrets, kept in Vaultwarden:
   - `openssl rand -hex 32`: the RPC secret (must be hex);
   - `openssl rand -base64 32`, twice: the admin and metrics tokens;
   - `echo GK$(openssl rand -hex 16)` and `openssl rand -hex 32`: the backup key's ID and
     secret. Put these two in the `cluster-secrets` item as `CNPG_BACKUP_S3_ACCESS_KEY_ID` and
     `CNPG_BACKUP_S3_SECRET_ACCESS_KEY`: the cluster reads them from there, Garage from the
     stack's variables of the same names.

## Variables (the stack's environment in Dockhand)

Mark every one **secret**, as for the other stacks; `.env` lists them blank for Populate.

| Variable | Value |
|---|---|
| `GARAGE_BIND_ADDRESS` | the NAS's address the cluster reaches it on (the S3 API, port 3900) |
| `GARAGE_DIR` | the folder from step 1, e.g. `/Volume1/garage` (holds `meta/` and `data/`) |
| `GARAGE_RPC_SECRET` | secret: the hex value |
| `GARAGE_ADMIN_TOKEN`, `GARAGE_METRICS_TOKEN` | secret: the two base64 values |
| `CNPG_BACKUP_S3_ACCESS_KEY_ID`, `CNPG_BACKUP_S3_SECRET_ACCESS_KEY` | secret: the backup key's ID and secret |

## What it sets up by itself

Started with `--single-node --default-bucket`, Garage does at every start whatever is still
missing: the single-node layout (its capacity is the data folder's disk size), the access key
`CNPG_BACKUP_S3_ACCESS_KEY_ID` with its secret, and the bucket `cnpg-backups`, with that key
allowed to read, write and own it. Nothing to run after the deploy.

- **It refuses to start** if the key already exists with a different secret, or the layout has
  been changed by hand past its first version (then drop `--single-node` and manage the layout
  with `garage layout`). To change the key's secret, create a new key ID too.
- **Check it** on the NAS: `docker exec garage /garage status` shows one node with a role in
  zone `dc1`, and `docker exec garage /garage bucket info cnpg-backups` lists the key with
  read, write and owner. From a machine that reaches the NAS, `curl -s http://<NAS address>:3900`
  answers with an S3 error document (`AccessDenied`): the API is up.

## Updating

Change the image tag in `compose.yaml` and redeploy. Read Garage's release notes first: a
major version can change the metadata format, and its automatic metadata snapshots
(`metadata_auto_snapshot_interval`) are what to fall back on.
