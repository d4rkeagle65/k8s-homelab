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
paths on the NAS. Garage's config is inline in `compose.yaml` instead: the `garage-config`
service writes it into a volume at every deploy, before Garage starts (its header says why).

## Before the first deploy (once)

1. **A folder for Garage** on a NAS volume (over SSH), with `meta/` and `data/` in it, for example
   `/Volume1/garage` (TOS's volume paths). Put `meta/` on an SSD if the NAS has one.

   ```sh
   mkdir -p /Volume1/garage/meta /Volume1/garage/data
   ```

2. **Five random values** for the stack's secrets, into Key Vault under the names in the table
   below (`_` as `-`), with a copy in Vaultwarden:
   - `openssl rand -hex 32`: the RPC secret (must be hex);
   - `openssl rand -base64 32`, twice: the admin and metrics tokens;
   - `echo GK$(openssl rand -hex 16)` and `openssl rand -hex 32`: the backup key's ID and
     secret. The cluster reads these two from Key Vault too, under the same names.
3. **The secret provider:** bind the Key Vault as the stack's secret provider in Dockhand,
   with its selector empty. `.env` holds the references.

## Variables (Key Vault, referenced from `.env`)

| Variable | Value |
|---|---|
| `TERRAMASTER_MGMT_ADDRESS` | the NAS's management address, which the cluster reaches it on (the S3 API, port 3900) |
| `GARAGE_DIR` | the folder from step 1, e.g. `/Volume1/garage` (holds `meta/` and `data/`) |
| `GARAGE_RPC_SECRET` | the hex value |
| `GARAGE_ADMIN_TOKEN`, `GARAGE_METRICS_TOKEN` | the two base64 values |
| `CNPG_BACKUP_S3_ACCESS_KEY_ID`, `CNPG_BACKUP_S3_SECRET_ACCESS_KEY` | the backup key's ID and secret |

## What it sets up by itself

Started with `--single-node --default-bucket`, Garage does at every start whatever is still
missing: the single-node layout (its capacity is the data folder's disk size), the access key
`CNPG_BACKUP_S3_ACCESS_KEY_ID` with its secret, and the bucket `cnpg-backups`, with that key
allowed to read, write and own it. Nothing to run after the deploy.

- **It refuses to start** if the key already exists with a different secret, or the layout has
  been changed by hand past its first version (then drop `--single-node` and manage the layout
  with `garage layout`). To change the key's secret, create a new key ID too.
- **Check it** on the NAS: `docker exec garage /garage -c /etc/garage/garage.toml status`
  shows one node with a role in zone `dc1`, and `docker exec garage /garage -c
  /etc/garage/garage.toml bucket info cnpg-backups` lists the key with
  read, write and owner. From a machine that reaches the NAS, `curl -s http://<NAS address>:3900`
  answers with an S3 error document (`AccessDenied`): the API is up.

## More buckets

Garage makes only `cnpg-backups` and its key by itself. Another bucket is made once on the
NAS, with a key Garage creates, allowed on that bucket alone (mariadb-operator's backups,
`QUEUE-mariadb.md`):

```sh
g="docker exec garage /garage -c /etc/garage/garage.toml"
$g bucket create mariadb-backups
$g key create mariadb-backups
$g bucket allow --read --write --owner mariadb-backups --key mariadb-backups
$g key info --show-secret mariadb-backups
```

The last command shows the key's ID and secret, which go into Azure Key Vault for
`cluster-secrets`. Let Garage create the key: `key import` with an ID made like the backup
key's (`GK` and 32 hex) didn't add one. These buckets and keys aren't recreated by Garage's
start-up: if its metadata is lost, make them again, with new Key Vault values.

## Updating

Change the image tag in `compose.yaml` and redeploy. Read Garage's release notes first: a
major version can change the metadata format, and its automatic metadata snapshots
(`metadata_auto_snapshot_interval`) are what to fall back on.
