# Semaphore on the Docker host

A web UI for running Ansible (and OpenTofu), from `compose.yaml`: Semaphore and its own
PostgreSQL. Its playbooks are this repo's `ansible/` folder, pulled from git. The inventory,
the SSH key and any secrets live only in Semaphore's database, encrypted with
`SEMAPHORE_ACCESS_KEY_ENCRYPTION`, never in git.

It holds an SSH key with root on the Proxmox hosts, so it's the most powerful credential in
the setup: the web UI is on the host's management address only, served through Traefik at
`semaphore.<domain>` to private networks only, and logins go through Authentik, admins only.

## Before the first deploy (once, as root on the host)

1. **Folders.** The container runs as uid 1001, group 0:

   ```sh
   install -d -m 750 -o 1001 -g 0 /opt/semaphore/config /opt/semaphore/data /opt/semaphore/tmp
   ```

   ```sh
   install -d -m 700 /opt/semaphore/postgres
   ```

2. **Pin the Proxmox hosts' SSH keys.** The image turns host-key checking off for every host;
   this file is what Semaphore checks against instead. Run it from the host, which reaches the
   management network, with each Proxmox host's management address:

   ```sh
   ssh-keyscan -t ed25519 <host 1> <host 2> <host 3> <host 4> > /opt/semaphore/config/known_hosts
   ```

   ```sh
   chown 1001:0 /opt/semaphore/config/known_hosts && chmod 640 /opt/semaphore/config/known_hosts
   ```

   Compare each key's fingerprint with the host's own before trusting it
   (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the host).

3. **Three random keys** for the stack's variables (run it three times):

   ```sh
   head -c32 /dev/urandom | base64
   ```

## Variables (Key Vault, referenced from `.env`)

Each is a Key Vault secret, `_` as `-`, read through the vault bound as the stack's secret
provider in Dockhand, with its selector empty. One waits in Dockhand, marked secret:
`SEMAPHORE_OIDC_CLIENT_SECRET`, the plain value of Key Vault's
`AUTHENTIK-SEMAPHORE-OIDC-CLIENT-SECRET` (held base64 there for now), the same one Authentik's
blueprint reads.

| Variable | Value |
|---|---|
| `DOCKER_VM_1_MGMT_ADDRESS` | the host's management address |
| `BASE_DOMAIN` | the domain: `semaphore.<domain>`, `auth.<domain>` and `netbox.<domain>` are built from it |
| `SEMAPHORE_DB_PASSWORD` | a new random password |
| `SEMAPHORE_ADMIN_PASSWORD` | the built-in admin's |
| `SEMAPHORE_ADMIN_EMAIL` | the built-in admin's email: **not** your Authentik email. Semaphore matches an Authentik login to users by email and refuses a local one, so with the same address you can't log in through Authentik (e.g. `you+semaphore-admin@<domain>`) |
| `SEMAPHORE_ACCESS_KEY_ENCRYPTION` | a random key from step 3. Losing it loses every stored key and secret; keep a copy in Vaultwarden |
| `SEMAPHORE_COOKIE_HASH`, `SEMAPHORE_COOKIE_ENCRYPTION` | the other two random keys |
| `TFSTATE_DB_PASSWORD` | a new random password for the `tfstate` role (OpenTofu's state) |
| `DATABASUS_SEMAPHORE_DB_PASSWORD` | a new random password for the read-only `databasus` role (`docker/databasus/README.md`) |
| `TIMEZONE` | the timezone schedules run in, e.g. `America/New_York` |
| `NETBOX_SEMAPHORE_TOKEN` | a NetBox token with writes off, for the NetBox inventory |

## First login

1. Deploy the stack in Dockhand, then open `https://semaphore.<domain>`.
2. Log in once with **Authentik**: Semaphore creates your user, named by your email, as an
   ordinary user. Its OIDC can't make anyone an admin. (If it sends you back to the login page
   and its log says the user "conflicts with local user", the built-in admin has your email:
   change the admin's email in Semaphore, and Key Vault's `SEMAPHORE-ADMIN-EMAIL` to match.)
3. Log out, log in as the built-in `admin` with `SEMAPHORE_ADMIN_PASSWORD`, and under Users
   tick **Admin** on your account.
4. From then on, log in with Authentik. The built-in admin is the fallback when Authentik is
   down; keep its password in Vaultwarden.

Setting up the project (repository, inventory, key, task templates) is in `ansible/README.md`.

## OpenTofu state

`tfstate-db-init` keeps a `tfstate` database and role in Semaphore's PostgreSQL, for OpenTofu's
`pg` backend: it creates them when missing and sets the role's password from
`TFSTATE_DB_PASSWORD` on every deploy, then exits (its log ends `tfstate database owned by
tfstate`). OpenTofu runs inside the Semaphore container, which reaches the database as
`semaphore-postgres`; nothing publishes it. The state holds secrets, so it lives here, outside
git and outside the cluster it describes. Semaphore's side (the environment carrying
`PG_CONN_STR`) is in `tofu/README.md`.

## Updating

Change the image tag in `compose.yaml` and redeploy. Semaphore migrates its database on
start, one way; back up `/opt/semaphore/postgres` first for a major version.
