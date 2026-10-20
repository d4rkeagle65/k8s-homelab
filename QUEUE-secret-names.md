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
- **A Dockhand stack moves in one redeploy, its secret provider bound first.** Its move's
  `.env` holds its references, which Dockhand resolves at each deploy, so binding the vault
  as its provider (selector empty) is the one step before the merge, which redeploys it
  (Flux calls its webhook; one without is redeployed by hand). Its old variables go on the
  delete list once it reads back working.
- **Base64 becomes plain with no refresh in between.** The template run on a value still
  base64 encodes it twice, and a plain value read as base64 can decode without an error into
  a wrong credential, which an app then uses (Authentik's blueprints set client secrets from
  them). So nothing may refresh `cluster-secrets` between the owner's rewrite of a value and
  its template reaching the cluster. What External Secrets 2.12 does (proved on scratch
  ExternalSecrets, the checklist has the evidence):
  - **`refreshPolicy: OnChange` holds**: no periodic refresh, whatever `refreshInterval` says.
    It refreshes once for each change to the ExternalSecret's generation, labels or
    annotations (`status.syncedResourceVersion` is the generation and a hash of all of
    them), and once whenever its Secret's data is edited. A Flux apply that changes nothing
    does not refresh it, nor does a new `main` revision. `scripts/keyvault-secrets.ps1`'s
    force-sync refreshes it twice: when its `force-sync` annotation is set, and when Flux
    strips it (it is `kubectl-annotate`'s) at its next reconcile of `cluster-secrets`.
  - **A template key overrides the data key of the same name** under `mergePolicy: Merge`,
    because `template.data` is written last: data `TIMEZONE` and template
    `TIMEZONE: "{{ .TIMEZONE | b64enc }}"` give base64 of the plain value. So every variable
    keeps its name.
  - A variable moving to a new name needs no hold when its new Key Vault secret already
    holds the plain value: its entry and its template land in one pull request, then the
    manifests move to it.
- **The switch: tooling first, then one hold, then batches.** Before the first batch, in
  pull requests of their own:
  - `scripts/tests/test_structure.py`'s `test_every_substituted_variable_is_defined` accepts
    a template key named after its own field when the template is exactly
    `{{ .NAME | b64enc }}`. Today it refuses a built variable with a field's name, and its
    sources pattern doesn't read a `|` pipe.
  - `scripts/keyvault-secrets.ps1` treats a variable the live template encodes as held
    plain: no encoding on a set, no decoding on `-Get`. Otherwise `-Get` can decode a plain
    value into a wrong one without an error, and a set writes base64 for the template to
    encode again.
  - `scripts/keyvault_copy.py` compares such a variable's decoded value, or it goes: as it
    stands, its `--apply` writes the Secret's base64 back into Key Vault, which the
    template then encodes twice.

  Then:
  1. **The hold**: a pull request sets `refreshPolicy: OnChange` on `cluster-secrets`. Its
     merge is one refresh, with values unchanged. Read back: `syncedResourceVersion` starts with
     the new generation, and `refreshTime` stays put for more than 15 minutes. It stays on
     through every batch. While it is on, a value changed in Key Vault reaches the cluster
     only through a force-sync or a spec change.
  2. **Each batch** (a few variables, one app's to start) is a pull request that adds
     `NAME: "{{ .NAME | b64enc }}"` to the template for each variable, leaving the `data`
     entries unchanged. It is opened and passes its suite, but is not merged yet.
  3. **Just before the rewrite**, read: the SHA-256 of every key in the `cluster-secrets`
     Secret (the fingerprint); no `force-sync` annotation on the ExternalSecret; its
     `syncedResourceVersion` matching its generation; and no other pull request touching
     `cluster-secrets.yaml` about to merge (its merge would refresh).
  4. **The owner rewrites** each variable's Key Vault value from base64 to plain, without a
     force-sync, read back by hash:
     `$v = ./scripts/keyvault-secrets.ps1 NAME -Get; ./scripts/keyvault-secrets.ps1 NAME -Value $v -Raw -NoSync`.
     Afterwards `refreshTime` is unchanged.
  5. **The owner merges the batch's pull request** at once: its spec change is the one
     refresh.
  6. **Read back**: `refreshTime` moved, `SecretSynced` for the new generation, and every key's
     hash equal to the fingerprint. That proves the Key Vault values are plain, since the
     template encodes them back to what they were, and that nothing downstream changes. If
     `refreshTime` moves between 4 and 5, merging the batch's pull request is the repair:
     then this read-back, and a restart of any app whose Secret Flux rewrote in between.

  The last batch's pull request also restores `refreshPolicy: Periodic`. Read back:
  `refreshTime` advancing every 15 minutes.
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
      the delete list below. Left: `PIHOLE_1_PASSWORD` and `PIHOLE_2_APP_PASSWORD`, which the
      manifests read base64. They are the switch's renamed pair, below
- [x] The base64-to-plain method proved (#147, 2026-10-20) on two scratch ExternalSecrets
      reading `TIMEZONE` alone, read back as timestamps and hashes only. OnChange:
      `refreshTime` stayed put for 10 minutes across ten 1m intervals, and the object was
      not written once, through a no-op Flux reconcile and a new `main` revision. A Periodic 1m
      control beside it refreshed every minute. Then each of these caused one refresh: a
      spec patch, Flux putting it back, a `force-sync` annotation, Flux stripping it, and an
      edit to its Secret's data (it removed the stray key). Afterwards it stayed put again. The
      override: the Secret's `TIMEZONE` is base64 of `cluster-secrets`' `TIMEZONE`, and it is
      the only key; the fallback (`TIMEZONE_PLAIN`) gave the same. External Secrets' v2.12.0
      source agrees (`shouldRefresh`, `GetResourceVersion`, `isSecretValid`, and
      `ApplyTemplate` writing `template.data` last). Not tried live: a controller restart,
      which the source shows forces no refresh. The scratch objects are out (#151). Read back
      2026-10-20: both ExternalSecrets and their Secrets gone; `cluster-secrets` untouched
      (its generation unchanged, `SecretSynced`); its Kustomization Ready
- [ ] The switch's three tooling changes (Rules), each a pull request of its own, all three
      open and passing their suites (2026-10-20), for the owner to merge before the hold: the
      structure test (#152); `scripts/keyvault-secrets.ps1` (#153), proved only on a mock
      vault and a fake kubectl, so its first set or `-Get` after a batch merges is its live
      check; `scripts/keyvault_copy.py` (#154), which also leaves a value rewritten plain
      before its template is live (reported `plain`), beyond what the Rules asked. Retiring
      the copy instead is the owner's call
- [ ] The hold (Rules' "The switch", step 1), merged just before the pilot
- [ ] The batches, each one pull request, in this order. 45 variables are held base64, and
      every one is listed here. They are ordered by what a wrong value would do, mildest first:
  - [ ] Pilot: `MEALIE_OIDC_CLIENT_ID` alone. A wrong value only fails Mealie's SSO login,
        and nothing writes it anywhere
  - [ ] 1 (12), each read only by its own app: `MEALIE_OIDC_CLIENT_SECRET`; babybuddy MCP's
        `BABYBUDDY_MCP_SECRETS_*` (4); Immich power tools' `IMMICH_POWER_TOOLS_APIKEY_*` (4;
        `_IMMICH_APIKEY` is Homepage's too); `NOIP_DUC_CREDENTIALS_*` (2);
        `CERT_MANAGER_CLOUDFLARE_API_TOKEN_API_TOKEN` (a failed renewal retries, with weeks to
        spare)
  - [ ] 2 (8), the Authentik OIDC client secrets:
        `AUTHENTIK_{DOCKHAND,GRAFANA,GRIMMORY,HOMEPAGE,NETBOX,ORGANIZR,SEMAPHORE,STORK}_OIDC_CLIENT_SECRET`.
        Authentik's blueprints write them into Authentik, so a wrong one breaks that app's
        login until it is put right. NETBOX, SEMAPHORE and STORK are the three Dockhand
        shares: once they are plain, its stacks can read them, and `dockhand_keyvault_copy.py`
        stops counting any name as held base64
  - [ ] 3 (5), the *arr API keys: `MEDIA_{BOOKSHELF,LIDARR,PROWLARR,RADARR,SONARR}_API_KEY`.
        Each app sets its own key from one, and Homepage, exportarr and organizr-sync read it
  - [ ] 4 (8), connections: `CLOUDFLARE_TUNNEL_SECRET_*` (3; the tunnel carries everything the
        internet reaches), `REDIS_HA_CREDS_*` (2), `AUTHENTIK_REDIS_PASSWORD_PASSWORD`,
        `IMMICH_REDIS_PASSWORD_PASSWORD`, `IMMICH_REDIS_URL_REDIS_URL`
  - [ ] 5 (9), last, and it restores `Periodic`. A wrong value here would change stored state:
        `IMMICH_POSTGRES_SUPERUSER_*` (2; CloudNativePG sets the superuser's password from it,
        `enableSuperuserAccess`), `OBSIDIAN_COUCHDB_*` (4), `AUTHENTIK_SECRET_KEY_PASSWORD`
        (Authentik's secret key: its sessions and what it encrypts),
        `AUTHENTIK_SECRET_BOOTSTRAP_*` (2)
  - [ ] The two renamed, with no hold (Rules), in the order of "A name reaches Key Vault
        before anything reads it". `PIHOLE_PASSWORD` becomes `PIHOLE_1_PASSWORD`:
        `PIHOLE-1-PASSWORD` is `PIHOLE-PASSWORD`'s plain form (by hash, 2026-10-20), so its
        entry and template land in one pull request, then Homepage's, nebula-sync's and
        pihole-exporter's manifests move to it. `PIHOLE2_PASSWORD` becomes
        `PIHOLE_2_APP_PASSWORD`, which Key Vault lacks, so the owner adds it first:
        `$v = ./scripts/keyvault-secrets.ps1 PIHOLE2_PASSWORD -Get; ./scripts/keyvault-secrets.ps1 PIHOLE_2_APP_PASSWORD -New -Value $v -Raw`
- [ ] Each Dockhand stack on its standard names and references. cadvisor first, done but for
      its leftovers: its compose file reads `DOCKER_VM_1_MGMT_ADDRESS` (#140), set in Dockhand
      as `azurekv://DOCKER-VM-1-MGMT-ADDRESS`; read back 2026-10-20: redeployed by its webhook
      at 14:12Z, port 9338 published on the management address (the owner, from its ports
      line), Prometheus's `docker-vm` target `up`; its leftovers are on the delete list below.
      A variable compose interpolates (as in `ports:`) never reaches the container's environment, so
      `printenv` inside it shows neither name; the ports line is the check. kea-standby
      second, done but for its leftovers: its compose file reads `DOCKER_VM_2_PROD_ADDRESS` and
      `DOCKER_VM_2_MGMT_ADDRESS` (#146), set as references and redeployed by hand (no webhook:
      a Hawser stack on the second VM). Its first deploy failed, "Invalid environment variable
      name": a secret selector on the stack had pulled the whole vault (cleared; the rule is in
      `docker/CLAUDE.md`). Read back 2026-10-20: Kea's API port 8000 open on the management
      address and refused on the production one, as before the move; the primary sees it in
      hot-standby, in touch, heard from 4 s before. tailscale third, with the `.env` of
      references every moved stack now has (cadvisor's and kea-standby's too; #150):
      `TAILSCALE_AUTH_KEY`, `_TAGS`, `_HOSTNAME`, `_ROUTES`. Read back 2026-10-20: the owner
      sees it up; the redeploy after the merge recreated it at 16:49Z (same image), its four
      Key Vault values are Dockhand's by hash, and it hasn't restarted since, as an unresolved
      `TS_ROUTES` would make it; cadvisor not recreated, its target `up`. A new start time
      alone doesn't mean a reference failed: Dockhand also recreates on a forced redeploy
      (Recreate all, the stack's always-redeploy setting, a retry after a failed deploy).
      Left: on the host, `docker inspect tailscale --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -c azurekv`
      gives 0 when every reference resolved
- [ ] New values plain too, once the switch is done. `scripts/README.md`'s "A new private
      value" puts a value a Secret's `data:` uses into Key Vault as its base64, and capture
      lists a promoted Secret's values base64 in `kubernetes/.local/vaultwarden-pending.yaml`
      (`promote.py`'s "Secrets" comment, `varsub.py`'s `_write_entries`). Left so, each Secret
      promoted afterwards adds a base64 value, and "every value in Key Vault is plain" (Done
      when) stops being true: each new one wants its plain value and a
      `{{ .NAME | b64enc }}` template entry
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
  `DOCKER_VM_2_PROD_ADDRESS` (#146)
- tailscale's `TS_AUTHKEY`, `TS_TAGS`, `TS_HOSTNAME` and `TS_ROUTES`: now `TAILSCALE_*` (#150)
- cadvisor's `DOCKER_VM_1_MGMT_ADDRESS` and kea-standby's two references, set by hand in
  Dockhand: their `.env`s carry the same references now
- To come: each stack's old names once it has moved (`docker/secret-names.yaml` lists them)

## Resuming

`scripts/keyvault-secrets.ps1` lists each cluster variable with its Key Vault name and
encoding (`| Where-Object Encoding -eq base64` gives the switch's variables still to move); a
Dockhand stack's `.env` names its variables. The first unticked box is next.

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
