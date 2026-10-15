# NetBox

NetBox on the Docker host: the homelab's documentation, and the source of Semaphore's
inventories (`QUEUE-netbox.md`). It runs here rather than in the cluster because OpenTofu
builds the cluster's VMs and will read NetBox to do it.

- **The image is built here** from the `Dockerfile`: the upstream NetBox image plus the
  plugins pinned in `plugin_requirements.txt`, with `config/plugins.py` (the one place
  `PLUGINS` and `PLUGINS_CONFIG` are set) and `config/sso.py` added beside the image's own
  configuration. The stack has "Build images on deploy" on in Dockhand, so a change to any of
  them rebuilds it.
- **Logins go through Authentik's OIDC**, and Authentik's groups decide NetBox's
  (`custom_pipeline.py`): a member of `authentik Admins` is a superuser. Only authentik Admins
  may log in (the blueprint's binding).
- **Scripts and tools use API tokens** through `netbox.<domain>/api`, which is served
  without a login page.

## First deploy

1. **The secrets**, each in Vaultwarden first, then in the stack's environment in Dockhand
   (`compose.yaml` lists the variables): `NETBOX_DB_PASSWORD`, `NETBOX_SECRET_KEY` and
   `NETBOX_API_TOKEN_PEPPER` (each `head -c 48 /dev/urandom | base64`), `OIDC_CLIENT_SECRET`
   (the Vaultwarden field `AUTHENTIK_NETBOX_OIDC_CLIENT_SECRET`, which Authentik's blueprint
   reads too), and `DATABASUS_DB_PASSWORD` (the same as the other stacks').
2. **The stack** in Dockhand from this folder, with "Build images on deploy" on, and its
   webhook enabled with `DOCKHAND_WEBHOOK_SECRET` (`docker/CLAUDE.md`).
3. **The first start** migrates an empty database; give it a few minutes before the health
   check passes. Then log in through Authentik.
4. **Backups:** add the `netbox` database to Databasus (host `netbox-postgres` on the stack's
   network, user `databasus`), as for the other stacks' databases.

## Upgrades

Renovate offers the base image and each plugin. A NetBox minor upgrade waits until every
plugin supports it; until then the plugin's own release refuses to load and NetBox won't
start. Each upgrade migrates the database on the first start after the rebuild: Databasus's
latest backup is the way back.

## The Kea plugin

Its Kea servers are set in NetBox's UI. NetBox reaches Kea's control agent over the Docker
network, not through the host's `127.0.0.1`, which inside a container is the container
itself: join this stack to the Kea stack's network when the first server is added.
