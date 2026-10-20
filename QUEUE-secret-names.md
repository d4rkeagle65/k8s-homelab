# Secret names: one standard, one secret per thing

**The ask (2026-10-19):** one naming standard for every secret the cluster and Dockhand read
from Azure Key Vault. Where both touch the same thing, they read one secret, and a file that
reads it under another name (`${TZ}`) is changed to the standard one (`${TIMEZONE}`).

## Settled

The owner, 2026-10-19:

- **One thing, one secret.** The cluster and a Dockhand stack that use the same credential or
  setting read the same Key Vault secret. Two credentials are two secrets, whatever their
  values: equal values are a reason to look, never a reason to merge. A fact given to many
  apps (the owner's address) is one.
- **`<SYSTEM>_<THING>`, named for the system the value belongs to**, not for the app that
  reads it. Pi-hole 1's password is `PIHOLE_1_PASSWORD` for Homepage, nebula-sync and netbox
  alike; a client secret Authentik issues is `AUTHENTIK_<APP>_OIDC_CLIENT_SECRET`; a NetBox
  token is `NETBOX_<CONSUMER>_TOKEN`; databasus's login on X's database is
  `DATABASUS_<X>_DB_PASSWORD`. In Key Vault each `_` is `-`.
- **No generic names.** `TZ`, `DOMAIN`, `ADMIN_TOKEN`, `POSTGRES_PASSWORD`, `OIDC_*`,
  `SSO_*` and `TS_*` take their system's prefix. Facts about the whole setup stand alone:
  `TIMEZONE`, `BASE_DOMAIN`, `CLUSTER_DOMAIN`, `OWNER_EMAIL`.
- **An instance number is its own word:** `<SYSTEM>_<N>_<THING>` (`DOCKER_VM_1_MGMT_ADDRESS`,
  `PIHOLE_2_PASSWORD`). There are two Docker VMs; kea-standby runs on the second.
- **A host's address is one variable**, `<HOST>_<N>_<NETWORK>_ADDRESS`, the `_<N>` only where
  there are several (`DOCKER_VM_1_MGMT_ADDRESS`, `TERRAMASTER_MGMT_ADDRESS`); a service binds
  to its host's, never to one of its own. Each address below was checked 2026-10-19 to be on
  the network its name says.
- **Built, not stored:** a URL or hostname under the domain is written in the file from
  `${BASE_DOMAIN}`, as the cluster's manifests do.
- **What is fine in git stays in git:** Docker network names and flags are written into the
  compose file, not kept as variables.
- **Key Vault holds every value plain, all of them.** Where a manifest needs base64 (a
  Secret's `data:`; the Encoding column of `scripts/keyvault-secrets.ps1`), the
  `cluster-secrets` ExternalSecret's template encodes it, so a value reads the same to
  Dockhand and to the cluster.
- **Renamed only where shared.** A variable only the cluster reads keeps its name, fitting the
  standard or not (`PIHOLE1_MGMT_ADDRESS`; the CNPG backup keys, already one name on both
  sides), and so do the names `promote.py` generates (`<namespace>-<secret>_<key>`). New names
  follow the standard.
- **A Dockhand stack variable is an inline reference under its standard name**:
  `TIMEZONE = azurekv://TIMEZONE`. This replaces `scripts/dockhand_keyvault_copy.py`'s
  `<stack>--<KEY>` names, which nothing has been written under.
- **Pi-hole 2 has two credentials**, both signing in (netbox-dns-sync healthy after a restart,
  2026-10-19): `PIHOLE_2_PASSWORD`, its web password, the one typed at its login page, and
  `PIHOLE_2_APP_PASSWORD`, its app password. The cluster's `PIHOLE2_PASSWORD` (the app
  password) is renamed though only the cluster reads it: beside `PIHOLE_2_PASSWORD` a name
  differing by one `_` would invite the wrong one.
- **Mail goes through the cluster's relay**, `smtp-relay.${BASE_DOMAIN}` (built, not stored),
  which holds no credentials: each sender signs in with its own Entra app, as
  `ENTRA_<APP>_SMTP_USERNAME` (`tenant_id@client_id`) and `ENTRA_<APP>_SMTP_CLIENT_SECRET`.

## The names

**Merged, one secret serving both sides:**

| Standard | Cluster today | Dockhand today |
|---|---|---|
| `TIMEZONE` | `TIMEZONE` | semaphore's and netbox's `TZ` (both compared equal) |
| `OWNER_EMAIL` | `ACME_EMAIL`, `ORGANIZR_ADMIN_EMAIL`, `ZAMMAD_ADMIN_EMAIL` | vaultwarden's `SMTP_FROM` |
| `BASE_DOMAIN` | `BASE_DOMAIN` | vaultwarden's `SIGNUPS_DOMAINS_WHITELIST`, as `${BASE_DOMAIN}` |
| `KEA_DB_PASSWORD` | `KEA_DB_PASSWORD` | kea's `KEA_DB_PASSWORD` |
| `AUTHENTIK_STORK_OIDC_CLIENT_SECRET` | the same (base64) | kea's `STORK_OIDC_CLIENT_SECRET` |
| `AUTHENTIK_SEMAPHORE_OIDC_CLIENT_SECRET` | the same (base64) | semaphore's `SEMAPHORE_OIDC_CLIENT_SECRET` |
| `AUTHENTIK_NETBOX_OIDC_CLIENT_SECRET` | the same (base64) | netbox's `OIDC_CLIENT_SECRET` |
| `PIHOLE_1_PASSWORD` | `PIHOLE_PASSWORD` (base64) | netbox's `PIHOLE_1_PASSWORD` |
| `PIHOLE_2_PASSWORD` | (the cluster reads the app password instead) | netbox's `PIHOLE_2_PASSWORD`, the web password |
| `PIHOLE_2_APP_PASSWORD` | `PIHOLE2_PASSWORD` (base64) | |
| `PIHOLE_1_URL`, `PIHOLE_2_URL` | `PIHOLE_URL`, `PIHOLE2_URL` | netbox's `PIHOLE_1_URL`, `PIHOLE_2_URL` (compared equal) |
| `GARAGE_S3_ENDPOINT` | `CNPG_BACKUP_S3_ENDPOINT` | vaultwarden's `BACKUP_S3_ENDPOINT` |
| `CNPG_BACKUP_S3_ACCESS_KEY_ID`, `..._SECRET_ACCESS_KEY`, `GARAGE_METRICS_TOKEN` | the same | garage's, the same names (compared equal) |
| `DOCKER_VM_1_MGMT_ADDRESS` | (Key Vault's `CADVISOR-ADDRESS`, unmapped) | cadvisor's `CADVISOR_ADDRESS`; databasus's, netbox's and semaphore's `*_BIND_ADDRESS`; kea's `KEA_API_ADDRESS` |
| `DOCKER_VM_1_PROD_ADDRESS` | | kea's `KEA_DHCP_ADDRESS`, `VAULTWARDEN_BIND_ADDRESS` |
| `DOCKER_VM_2_MGMT_ADDRESS`, `DOCKER_VM_2_PROD_ADDRESS` | | kea-standby's `KEA_API_ADDRESS`, `KEA_DHCP_ADDRESS` |
| `TERRAMASTER_MGMT_ADDRESS` | `TERRAMASTER_ADDRESS` | garage's `GARAGE_BIND_ADDRESS` (compared equal) |

**Dockhand's own, renamed or dropped** (kept as they are: databasus's `DATABASUS_AGENT_ID`,
`_AGENT_TOKEN`, `_URL`; kea's `STORK_DB_PASSWORD`; netbox's `NETBOX_API_TOKEN_PEPPER`,
`_DB_PASSWORD`, `_SECRET_KEY`; semaphore's `SEMAPHORE_*` and `TFSTATE_DB_PASSWORD`; garage's
`GARAGE_RPC_SECRET`, `GARAGE_ADMIN_TOKEN`, `GARAGE_DIR`):

| Stack | Today | Standard |
|---|---|---|
| databasus, kea, netbox, semaphore | `KEA_NETWORK` (one value in all four; kea's compose doesn't read it), `NETBOX_NETWORK`, `SEMAPHORE_NETWORK` | written into the compose files; kea's dropped |
| kea, netbox, semaphore | `DATABASUS_DB_PASSWORD` (not one value across the three) | `DATABASUS_KEA_DB_PASSWORD`, `DATABASUS_NETBOX_DB_PASSWORD`, `DATABASUS_SEMAPHORE_DB_PASSWORD` |
| kea | `POSTGRES_PASSWORD` | `KEA_POSTGRES_PASSWORD` |
| kea | `STORK_OIDC_ISSUER_URL`, `STORK_OIDC_REDIRECT_URI` | built from `${BASE_DOMAIN}` |
| netbox | `NETBOX_HOST`, `OIDC_ENDPOINT` | built from `${BASE_DOMAIN}` |
| netbox | `CONFIG_DIFF_USERNAME`, `CONFIG_DIFF_PASSWORD` (blank) | nothing to store; `NETBOX_CONFIG_DIFF_USERNAME`, `_PASSWORD` if ever set |
| netbox | `NETBOX_DNS_SYNC_TOKEN` (not the cluster's `NETBOX_DNS_TOKEN`) | kept: it fits `NETBOX_<CONSUMER>_TOKEN` |
| netbox | `PIHOLE_TLS_VERIFY`, `SYNC_APPLY`, `SYNC_DELETE` | written into the compose file |
| semaphore | `NETBOX_API`, `SEMAPHORE_OIDC_ISSUER_URL`, `SEMAPHORE_WEB_ROOT` | built from `${BASE_DOMAIN}` |
| semaphore | `NETBOX_TOKEN` (no cluster token holds it) | `NETBOX_SEMAPHORE_TOKEN` |
| tailscale | `TS_AUTHKEY`, `TS_HOSTNAME`, `TS_ROUTES`, `TS_TAGS` | `TAILSCALE_AUTH_KEY`, `TAILSCALE_HOSTNAME`, `TAILSCALE_ROUTES`, `TAILSCALE_TAGS` |
| vaultwarden | `ADMIN_TOKEN` | `VAULTWARDEN_ADMIN_TOKEN` |
| vaultwarden | `BACKUP_ZIP_PASSWORD` (blank) | nothing to store; `VAULTWARDEN_BACKUP_ZIP_PASSWORD` if ever set |
| vaultwarden | `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY` (not the CNPG keys) | `VAULTWARDEN_BACKUP_S3_ACCESS_KEY_ID`, `VAULTWARDEN_BACKUP_S3_SECRET_ACCESS_KEY` |
| vaultwarden | `DOMAIN`, `SSO_AUTHORITY` | built from `${BASE_DOMAIN}` |
| vaultwarden | `SSO_CLIENT_ID`, `SSO_CLIENT_SECRET` | `AUTHENTIK_VAULTWARDEN_OIDC_CLIENT_ID`, `AUTHENTIK_VAULTWARDEN_OIDC_CLIENT_SECRET` |
| vaultwarden | `SMTP_HOST` (compared equal to the built form) | `smtp-relay.${BASE_DOMAIN}` |
| vaultwarden | `SMTP_USERNAME`, `SMTP_PASSWORD` | `ENTRA_VAULTWARDEN_SMTP_USERNAME`, `ENTRA_VAULTWARDEN_SMTP_CLIENT_SECRET` |

## Rules

- **A name reaches Key Vault before anything reads it, and the old one goes last.** For a
  cluster variable: its Key Vault secret, then its `cluster-secrets.yaml` entry in a pull
  request of its own, then the manifests, then the old entry, then the old Key Vault secret
  (`CLAUDE.md`'s rule). A deleted name stays reserved for 90 days (purge protection), so a
  name is deleted only once nothing reads it.
- **A Dockhand stack moves in one redeploy, its new variables set first.** Merging its compose
  file's renames redeploys it (Flux calls its Dockhand webhook), so its variables under the
  new names, as `azurekv://` references, are set in Dockhand beside the old ones before that
  merge; the old ones go after it reads back working. One stack first: cadvisor, with one
  variable.
- **Base64 becomes plain with no refresh in between.** The template run on a value still
  base64 encodes it twice, and a plain value read as base64 can decode without an error into
  a wrong credential, which an app then uses (Authentik's blueprints set client secrets from
  them). So a value and its template change in the same refresh. The method, to be proved on
  a test ExternalSecret first (`QUEUE-azure-keyvault.md`'s rule): hold cluster-secrets'
  refreshes (`refreshPolicy: OnChange`) while the owner rewrites the values, then one pull
  request adds the templates and restores `Periodic`. Open until then: whether ESO 2.12
  honours `OnChange`, and whether a template key overrides the data key of the same name
  under `mergePolicy: Merge` (if not, the plain value comes in under a name of its own).
- **Which names hold equal values goes no further than the decisions above**: no value, and no
  list of logins sharing one, in this public file.

## Checklist

- [x] Compared (2026-10-19): Key Vault's secrets with every Dockhand stack variable (77 from 9
      stacks, once the owner had marked them all secret; netbox's `CONFIG_DIFF_*` and
      vaultwarden's `BACKUP_ZIP_PASSWORD` are blank), by hash and base64-decoded where held so,
      by `scripts/dockhand_keyvault_copy.py`'s dry run; where each is used, from the repo
- [x] The owner's decisions (2026-10-19), all in Settled and the tables
- [x] `Child2` and `MEDIA_WHISPARR_API_KEY` out of `cluster-secrets.yaml` (#135; nothing read
      them, no live Secret held the Whisparr v2 key, and the leak check keeps `Child2`'s value
      through `.secret-words`), read back 2026-10-19: the Secret holds neither, `SecretSynced`
      for its new spec, every Flux object Ready
- [ ] The owner deletes their Key Vault secrets, `Child2` and `MEDIA-WHISPARR-API-KEY` (both
      still there at the read-back)
- [ ] `scripts/dockhand_keyvault_copy.py` takes its names from this file's tables, counts a
      secret the cluster already holds with the same value as shared rather than a conflict,
      and its tests follow; `QUEUE-azure-keyvault.md`'s Dockhand items say so
- [ ] Key Vault: every name in the tables above that it doesn't hold yet, by the owner's
      copy run
- [ ] `cluster-secrets.yaml`: the renamed cluster variables mapped, a pull request of its own;
      then the manifests on them; then the old entries; then the old Key Vault secrets, by the
      owner
- [ ] Base64 to plain: the method proved on a test ExternalSecret, then in batches
- [ ] Each Dockhand stack on its standard names and references, cadvisor first; then Key
      Vault's `CADVISOR-ADDRESS` deleted by the owner
- [ ] The standard moved from Settled into `scripts/README.md`'s "Add or change a substitution
      variable", where a new name is chosen, once the first names follow it

## Resuming

`scripts/keyvault-secrets.ps1` lists each cluster variable with its Key Vault name and
encoding; a Dockhand stack's `.env` names its variables. The first unticked box is next.

## Done when

Every Key Vault secret has its standard name, nothing reads an old name, no two secrets hold
one thing, and every value in Key Vault is plain.

## Checked and negative

- **`DATABASUS_DB_PASSWORD` as one secret**: the three stacks' values aren't one value.
- **semaphore's `NETBOX_TOKEN` as one of the cluster's NetBox tokens**: none holds it.
- **netbox's `NETBOX_DNS_SYNC_TOKEN` as the cluster's `NETBOX_DNS_TOKEN`**: different tokens.
- **A Semaphore script reading netbox's Pi-hole passwords**: none; `netbox-dns-sync` is the
  only reader.
- **vaultwarden's S3 keys as the CNPG backup keys**: different keys.
- **Pi-hole 2's two values as an encoding artefact, or netbox's as a stale one**: both are
  clean, and both sign in; two credentials.
