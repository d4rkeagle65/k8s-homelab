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

## Variables (the stack's environment in Dockhand)

`.env` here lists every variable, blank, so Dockhand's **Populate** can fill in the names. Mark
every one **secret** in Dockhand, even the plain settings: Dockhand passes secret variables to
compose directly, and those override the blank file. Never put a value in `.env`.

| Variable | Value |
|---|---|
| `SEMAPHORE_BIND_ADDRESS` | the host's management address |
| `SEMAPHORE_WEB_ROOT` | `https://semaphore.<domain>` |
| `SEMAPHORE_DB_PASSWORD` | secret: a new random password |
| `SEMAPHORE_ADMIN_PASSWORD` | secret: the built-in admin's |
| `SEMAPHORE_ADMIN_EMAIL` | the built-in admin's email |
| `SEMAPHORE_ACCESS_KEY_ENCRYPTION` | secret: a random key from step 3. Losing it loses every stored key and secret; keep a copy in Vaultwarden |
| `SEMAPHORE_COOKIE_HASH`, `SEMAPHORE_COOKIE_ENCRYPTION` | secret: the other two random keys |
| `SEMAPHORE_OIDC_ISSUER_URL` | `https://auth.<domain>/application/o/semaphore/` |
| `SEMAPHORE_OIDC_CLIENT_SECRET` | secret: the same value as the `AUTHENTIK_SEMAPHORE_OIDC_CLIENT_SECRET` field in Vaultwarden (plain, not base64) |
| `TZ` | the timezone schedules run in, e.g. `America/New_York` |

## First login

1. Deploy the stack in Dockhand, then open `https://semaphore.<domain>`.
2. Log in once with **Authentik**: Semaphore creates your user, as an ordinary user. Its OIDC
   can't make anyone an admin.
3. Log out, log in as the built-in `admin` with `SEMAPHORE_ADMIN_PASSWORD`, and under Users
   tick **Admin** on your account.
4. From then on, log in with Authentik. The built-in admin is the fallback when Authentik is
   down; keep its password in Vaultwarden.

Setting up the project (repository, inventory, key, task templates) is in `ansible/README.md`.

## Updating

Change the image tag in `compose.yaml` and redeploy. Semaphore migrates its database on
start, one way; back up `/opt/semaphore/postgres` first for a major version.
