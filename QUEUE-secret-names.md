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
  `TIMEZONE = azurekv://TIMEZONE`. `docker/secret-names.yaml` gives each stack variable's name,
  and `scripts/dockhand_keyvault_copy.py` copies by it; the `<stack>--<KEY>` names it once
  proposed were never written.
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

| Standard | The cluster's old name | Dockhand's old name |
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

**Dockhand's own names**, each stack variable's in turn, are in `docker/secret-names.yaml`: its
standard name, the form its compose file builds it in from `${BASE_DOMAIN}`, or why it has
none (a setting written into the compose file, a blank). The copy checks each built form
against today's value by hash.

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
- [x] The owner deleted their Key Vault secrets, `Child2` and `MEDIA-WHISPARR-API-KEY`; read
      back 2026-10-20: both gone from the vault, their soft-deleted records kept by purge
      protection
- [x] `scripts/dockhand_keyvault_copy.py` copies by `docker/secret-names.yaml`, counts a name
      Key Vault already holds with the same value as shared, and never changes one; dry run
      2026-10-20 against the owner's export: no conflicts, 44 names to add, 5 held with the
      same value, 3 held base64 (the Authentik OIDC client secrets, waiting on the base64
      switch), all 12 built forms giving today's values
- [x] Key Vault: the names it lacked, by the owner's `--apply` run of the copy (2026-10-20, 44
      written, its read-back 44 of 44); read back after: a dry run finds none to add, 49 held
      with the same value and the 3 base64 OIDC client secrets, and all 44 carry
      `source=dockhand`
- [ ] `cluster-secrets.yaml`: the renamed cluster variables mapped, a pull request of its own;
      then the manifests on them; then the old entries; then the old Key Vault secrets, by the
      owner. The five held plain, `OWNER_EMAIL`, `GARAGE_S3_ENDPOINT`,
      `TERRAMASTER_MGMT_ADDRESS`, `PIHOLE_1_URL` and `PIHOLE_2_URL`, are done: mapped (#141),
      then every manifest moved to them and the seven old entries removed in one pull request
      (#143; capture writes the alphabetically first of two names holding one value, so both
      mapped would have turned the ClusterIssuer back to `${ACME_EMAIL}`). `flux diff` before
      it: no rendered change in any of the 18 Kustomizations it touched or `cluster-resources`
      (a deliberate wrong name, as a control, drifted). Read back 2026-10-20: `SecretSynced`
      for its new spec, none of the seven left and all five there, every Flux object Ready, no
      CNPG instance restarted, Loki's release unchanged. Their old Key Vault secrets are on
      the delete list below. Left: `PIHOLE_1_PASSWORD` and `PIHOLE_2_APP_PASSWORD`, waiting on
      the base64 switch, as the manifests read them base64
- [ ] Base64 to plain: the method proved on a test ExternalSecret, then in batches. The test is
      `kubernetes/secrets/keyvault-b64-test.yaml`, two scratch ExternalSecrets on `TIMEZONE`
      alone, answering Rules' two open questions; it comes out in a pull request of its own
      once they are answered, and Flux prunes both and their Secrets
- [ ] Each Dockhand stack on its standard names and references. cadvisor first, done but for
      its leftovers: its compose file reads `DOCKER_VM_1_MGMT_ADDRESS` (#140), set in Dockhand
      as `azurekv://DOCKER-VM-1-MGMT-ADDRESS`; read back 2026-10-20: redeployed by its webhook
      at 14:12Z, port 9338 published on the management address (the owner, from its ports
      line), Prometheus's `docker-vm` target `up`; its leftovers are on the delete list below.
      A variable compose interpolates (as in `ports:`) never reaches the container's environment, so
      `printenv` inside it shows neither name; the ports line is the check. kea-standby next:
      its compose file reads `DOCKER_VM_2_PROD_ADDRESS` and `DOCKER_VM_2_MGMT_ADDRESS` (branch
      `kea-standby-standard-names`). It has no webhook (a Hawser stack on the second VM), so
      the owner sets both references, merges, then redeploys it in Dockhand. Before: Kea's API
      port 8000 open on the VM's management address and refused on its production one, which
      the same probe after the redeploy must show again
- [ ] The standard moved from Settled into `scripts/README.md`'s "Add or change a substitution
      variable", where a new name is chosen, once the first names follow it

## Left to delete when the move is done

The owner deletes these once every stack and manifest reads its standard name. Nothing reads
any of them now unless its line says so. A deleted Key Vault name stays reserved for 90 days
(purge protection), so only what is on this list goes. Each step adds what it leaves behind.

**Key Vault secrets:**

- `ACME-EMAIL`, `ORGANIZR-ADMIN-EMAIL`, `ZAMMAD-ADMIN-EMAIL`: now `OWNER-EMAIL` (#143)
- `CNPG-BACKUP-S3-ENDPOINT`: now `GARAGE-S3-ENDPOINT` (#143)
- `TERRAMASTER-ADDRESS`: now `TERRAMASTER-MGMT-ADDRESS` (#143)
- `PIHOLE-URL`, `PIHOLE2-URL`: now `PIHOLE-1-URL`, `PIHOLE-2-URL` (#143)
- `CADVISOR-ADDRESS`: now `DOCKER-VM-1-MGMT-ADDRESS` (#140); never mapped into the cluster
- To come, still read by the cluster: `PIHOLE-PASSWORD` and `PIHOLE2-PASSWORD`, once the
  base64 switch moves the cluster onto `PIHOLE-1-PASSWORD` and `PIHOLE-2-APP-PASSWORD`

**Dockhand stack variables:**

- cadvisor's `CADVISOR_ADDRESS`: now `DOCKER_VM_1_MGMT_ADDRESS` (#140)
- kea-standby's `KEA_API_ADDRESS` and `KEA_DHCP_ADDRESS`: now `DOCKER_VM_2_MGMT_ADDRESS` and
  `DOCKER_VM_2_PROD_ADDRESS`, once its move reads back
- To come: each stack's old names once it has moved (`docker/secret-names.yaml` lists them)

## Resuming

`scripts/keyvault-secrets.ps1` lists each cluster variable with its Key Vault name and
encoding; a Dockhand stack's `.env` names its variables. The first unticked box is next.

## Done when

Every Key Vault secret has its standard name, nothing reads an old name, no two secrets hold
one thing, every value in Key Vault is plain, and the delete list is empty.

## Checked and negative

- **`DATABASUS_DB_PASSWORD` as one secret**: the three stacks' values aren't one value.
- **semaphore's `NETBOX_TOKEN` as one of the cluster's NetBox tokens**: none holds it.
- **netbox's `NETBOX_DNS_SYNC_TOKEN` as the cluster's `NETBOX_DNS_TOKEN`**: different tokens.
- **A Semaphore script reading netbox's Pi-hole passwords**: none; `netbox-dns-sync` is the
  only reader.
- **vaultwarden's S3 keys as the CNPG backup keys**: different keys.
- **Pi-hole 2's two values as an encoding artefact, or netbox's as a stale one**: both are
  clean, and both sign in; two credentials.
