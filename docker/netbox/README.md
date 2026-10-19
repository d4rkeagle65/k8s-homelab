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
- **NetBox owns local DNS** (`old-queues/QUEUE-netbox-dns.md`): `netbox-dns-sync` (`dns-sync/`) copies
  NetBox DNS's records to every Pi-hole each minute. Its variables: `NETBOX_DNS_SYNC_TOKEN` (a
  read-only token), `PIHOLE_1_URL` and `PIHOLE_1_PASSWORD` (an app password; `PIHOLE_2_...`
  for the second), and its switches, off until set to `true`: `SYNC_APPLY` writes,
  `SYNC_DELETE` also removes a record in a NetBox zone that NetBox doesn't have.
  `PIHOLE_TLS_VERIFY=false` accepts a Pi-hole's self-signed certificate.

## First deploy

1. **The secrets**, each in Vaultwarden first, then in the stack's environment in Dockhand
   (`compose.yaml` says what each is; `.env` lists them blank for Dockhand's **Populate**). Mark
   every one **secret**, even the plain settings: Dockhand passes those to compose directly,
   and they override the blank file. Never put a value in `.env`. They are: `NETBOX_DB_PASSWORD`, `NETBOX_SECRET_KEY` and
   `NETBOX_API_TOKEN_PEPPER` (each `head -c 48 /dev/urandom | base64`), `OIDC_CLIENT_SECRET`
   (the Vaultwarden field `AUTHENTIK_NETBOX_OIDC_CLIENT_SECRET`, which Authentik's blueprint
   reads too), and `DATABASUS_DB_PASSWORD` (its own, the Vaultwarden field
   `NETBOX_DATABASUS_DB_PASSWORD`).
2. **The stack** in Dockhand from this folder, with "Build images on deploy" on, and its
   webhook enabled with `DOCKHAND_WEBHOOK_SECRET` (`docker/CLAUDE.md`).
3. **The first start** migrates an empty database; give it a few minutes before the health
   check passes. Then log in through Authentik.
4. **Backups:** once this stack is up, its network exists: add it to the Databasus stack as
   `NETBOX_NETWORK`, as `SEMAPHORE_NETWORK` and `KEA_NETWORK` are (before then, Databasus's
   redeploy would fail on the missing network). Then add the `netbox` database in Databasus
   (host `netbox-postgres`, user `databasus`, that same password).

## Upgrades

Renovate offers the base image and each plugin. A NetBox minor upgrade waits until every
plugin supports it; until then the plugin's own release refuses to load and NetBox won't
start. Each upgrade migrates the database on the first start after the rebuild: Databasus's
latest backup is the way back.

## The Kea plugin

Its Kea servers are set in NetBox's UI: the DHCPv4 server URL is `http://kea-dhcp4:8000` (Kea
3 serves its control API from the DHCP server itself), with the user and password from Kea's
`api-credentials`. NetBox reaches it over the kea stack's network, which this stack joins as
`KEA_NETWORK`: inside a container, `127.0.0.1` is the container itself, not the host.
