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
  as its provider (selector empty) is the step before the merge, which redeploys it (Flux
  calls its webhook; one without is redeployed by hand). A variable set in Dockhand under a
  reference's own name wins over the `.env`, so it is deleted just before the merge, or the
  stack keeps reading it and a broken reference shows only once it goes. Its other old
  variables go on the delete list once it reads back working.
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
    does not refresh it, nor does a new `main` revision or a restart of the controller. `scripts/keyvault-secrets.ps1`'s
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
     For a batch, use the loop in #166's description. It rewrites only a variable whose Key
     Vault value still equals what the `cluster-secrets` Secret holds, so a rerun after a stop
     never decodes a plain value. Then, before the merge, check by hash that every variable
     in the batch is plain in Key Vault and that `refreshTime` is unchanged.
  5. **The owner merges the batch's pull request** at once: its spec change is the one
     refresh.
  6. **Read back**: `refreshTime` moved, `SecretSynced` for the new generation, and every key's
     hash equal to the fingerprint. Read only once `syncedResourceVersion` starts with the new
     generation: the refresh can come seconds after Flux's apply, and a read before it proves
     nothing. An unchanged Secret `resourceVersion` says the same, since External Secrets
     doesn't write a Secret whose data is unchanged. That proves the Key Vault values are plain, since the
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
- [x] `cluster-secrets.yaml`: the renamed cluster variables mapped, a pull request of its own;
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
      the delete list below. `PIHOLE_1_PASSWORD` and `PIHOLE_2_APP_PASSWORD` followed as the
      switch's renamed pair (#187, #190), below
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
      `ApplyTemplate` writing `template.data` last). On `cluster-secrets` itself, a node
      drain replaced the controller (2026-10-20 18:16Z) and the webhook (18:21Z), and neither
      refreshed it. The scratch objects are out (#151). Read back
      2026-10-20: both ExternalSecrets and their Secrets gone; `cluster-secrets` untouched
      (its generation unchanged, `SecretSynced`); its Kustomization Ready
- [ ] The switch's three tooling changes (Rules), each a pull request of its own, all three
      open and passing their suites (2026-10-20), for the owner to merge before the hold: the
      structure test (#152); `scripts/keyvault-secrets.ps1` (#153), proved only on a mock
      vault and a fake kubectl, so its first set or `-Get` after a batch merges is its live
      check; `scripts/keyvault_copy.py` (#154), which also leaves a value rewritten plain
      before its template is live (reported `plain`), beyond what the Rules asked. Retiring
      the copy instead is the owner's call
- [x] The hold (Rules' "The switch", step 1): #161, merged 2026-10-20 17:33Z. Read back:
      `OnChange` at generation 65, one refresh at its apply with no key changed (the Secret was
      last written at 14:38Z), `SecretSynced`, then no refresh until the pilot's merge. The
      owner's rewrite came 5 minutes in, before step 1's 15 minutes were up; nothing refreshed
- [x] The batches, each one pull request, in this order. 45 variables were held base64, and
      every one is listed here. They are ordered by what a wrong value would do, mildest first:
  - [x] Pilot: `MEALIE_OIDC_CLIENT_ID` alone (#162, 2026-10-20). Read back, by hash only:
        Key Vault held the plain value from the owner's rewrite at 17:39Z; nothing refreshed
        before the merge at 17:42Z; then one refresh for generation 66, `SecretSynced`, all 145
        keys equal to the fingerprint, and the Secret not even rewritten. Mealie's
        `mealie-oidc` is unchanged (last written 2026-10-05)
  - [x] 1 (12), each read only by its own app: `MEALIE_OIDC_CLIENT_SECRET`; babybuddy MCP's
        `BABYBUDDY_MCP_SECRETS_*` (4); Immich power tools' `IMMICH_POWER_TOOLS_APIKEY_*` (4;
        `_IMMICH_APIKEY` is Homepage's too); `NOIP_DUC_CREDENTIALS_*` (2);
        `CERT_MANAGER_CLOUDFLARE_API_TOKEN_API_TOKEN`. #166, 2026-10-20. Read back by hash
        only: all 12 in the old base64 form before the rewrite; all 12 plain after it, with no
        refresh in between (the hold had gone 16 minutes without one first). Then one refresh
        for generation 67, `SecretSynced`, all 145 keys equal to the fingerprint, and the
        Secret not rewritten. The six Secrets these feed (babybuddy, cert-manager, immich,
        mealie, noip-duc, homepage) were last written days before
  - [x] 2 (8), the Authentik OIDC client secrets:
        `AUTHENTIK_{DOCKHAND,GRAFANA,GRIMMORY,HOMEPAGE,NETBOX,ORGANIZR,SEMAPHORE,STORK}_OIDC_CLIENT_SECRET`.
        #173, 2026-10-20. Read back by hash only: all 8 in the old base64 form before the
        rewrite; all 8 plain after it, with no refresh in between. Then one refresh for
        generation 68, `SecretSynced`, all 145 keys equal to the fingerprint, and the Secret
        not rewritten. The 12 Secrets these feed (the 8 in `authentik`, plus Grafana's,
        Grimmory's, Homepage's and Organizr's) were last written days before. NETBOX,
        SEMAPHORE and STORK are the three Dockhand shares, now plain, so kea's, netbox's and
        semaphore's stacks can read them by reference (each stack's move), and
        `dockhand_keyvault_copy.py` should count no name as held base64
  - [x] 3 (5), the *arr API keys: `MEDIA_{BOOKSHELF,LIDARR,PROWLARR,RADARR,SONARR}_API_KEY`.
        #175, 2026-10-20, after #176 (the Garage backup-monitor key's mapping) had merged and
        refreshed first, adding only its two keys. Read back by hash only: all 5 in the old
        base64 form before the rewrite; all 5 plain after it, with no refresh in between.
        Then one refresh for generation 70, `SecretSynced`, all 147 keys equal to the
        fingerprint, and the Secret not rewritten. The 8 Secrets these feed (the five apps',
        exportarr's, Homepage's, organizr-sync's) were last written days before
  - [x] 4 (8), connections: `CLOUDFLARE_TUNNEL_SECRET_*` (3), `REDIS_HA_CREDS_*` (2),
        `AUTHENTIK_REDIS_PASSWORD_PASSWORD`, `IMMICH_REDIS_PASSWORD_PASSWORD`,
        `IMMICH_REDIS_URL_REDIS_URL`. #180, 2026-10-20. Read back by hash only: all 8 in the old
        base64 form before the rewrite; all 8 plain after it, with no refresh in between. Then
        one refresh for generation 71, `SecretSynced`, all 147 keys equal to the fingerprint,
        and the Secret not rewritten. The 6 Secrets these feed (`cloudflare-tunnel-secret`,
        `redis-ha-creds`, Authentik's and Immich's `redis-password`, `immich-redis-url`,
        Homepage's) were last written days before; the tunnel's pods ready, never restarted
  - [x] 5 (9), stored state, and the hold's end: `IMMICH_POSTGRES_SUPERUSER_*` (2),
        `OBSIDIAN_COUCHDB_*` (4), `AUTHENTIK_SECRET_KEY_PASSWORD`, `AUTHENTIK_SECRET_BOOTSTRAP_*`
        (2), and `refreshPolicy` removed. #182, 2026-10-21. Read back by hash only: all 9 in
        the old base64 form before the rewrite; all 9 plain after it, with no refresh in
        between. Then one refresh for generation 72, `SecretSynced`, all 147 keys equal to the
        fingerprint, and the Secret not rewritten. The 4 Secrets these feed were last written
        2026-10-05, Immich's database healthy (2 of 2) and CouchDB steady. `refreshPolicy` is
        gone, and the next refresh came on its own 15 minutes later (00:52Z), the fingerprint
        unchanged
  - [x] The two renamed, with no hold: `PIHOLE_PASSWORD` to `PIHOLE_1_PASSWORD` and
        `PIHOLE2_PASSWORD` to `PIHOLE_2_APP_PASSWORD`. `PIHOLE-1-PASSWORD` was already the old
        value's plain form, and the owner added `PIHOLE-2-APP-PASSWORD` from the decoded old
        value (both by hash). #187 mapped both with their templates (generation 73, only the
        two keys added, each equal to its old name's). #190, 2026-10-21, moved Homepage's,
        nebula-sync's and pihole-exporter's Secrets and removed both old entries together,
        since capture writes the alphabetically first of two names holding one value. Its
        `flux diff`: no rendered change in the three (a wrong name, as a control, drifted).
        Read back against the fingerprint from before #187: the two old keys gone, the two
        new ones holding their values, the other 145 keys unchanged, `SecretSynced` at
        generation 74; the three Secrets last written days before, the four Kustomizations
        Ready. The old Key Vault secrets are on the delete list
- [ ] Each Dockhand stack on its standard names and references. cadvisor first, done but for
      its leftovers: its compose file reads `DOCKER_VM_1_MGMT_ADDRESS` (#140), set in Dockhand
      as `azurekv://DOCKER-VM-1-MGMT-ADDRESS`; read back 2026-10-20: redeployed by its webhook
      at 14:12Z, port 9338 published on the management address (the owner, from its ports
      line), Prometheus's `docker-vm` target `up`; its leftovers are on the delete list below.
      A variable compose interpolates (as in `ports:`) never reaches the container's environment, so
      `printenv` inside it shows neither name; the ports line is the check. kea-standby
      second, done: its compose file reads `DOCKER_VM_2_PROD_ADDRESS` and
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
      A reference left unresolved stays in the container's environment as text, so on the host
      `docker inspect <container> --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -c azurekv`
      gives 0 when every reference resolved: the owner ran it on every container on the
      Docker host on 2026-10-21, after #195's redeploy, and each gave 0. databasus fourth (#159):
      `DOCKER_VM_1_MGMT_ADDRESS`, `BASE_DOMAIN` (its URL is built in the compose file),
      `DATABASUS_AGENT_ID` and `DATABASUS_AGENT_TOKEN`, and its three networks written in as
      `<stack>_default` (the values, by hash); rendered with Key Vault's values, its compose
      file gave exactly the old one's. The owner deleted the agent's two Dockhand variables
      before the merge. Read back 2026-10-20: both containers recreated at 17:25Z (same image,
      as with tailscale: with the rendered file proven the same, start times are no check for
      these moves) and not restarting since, which the agent would if its URL hadn't resolved;
      port 4005 open on the management address and refused on the production one, as before;
      its URL answers 200 through Traefik. Left: the agent online in Databasus. garage fifth
      (#163): `TERRAMASTER_MGMT_ADDRESS` for `GARAGE_BIND_ADDRESS`; its other six keep their
      names, so the owner deleted them from Dockhand just before its redeploy (by hand: a
      Hawser stack, no webhook); rendered from its `.env`'s references, its compose file gave
      exactly the old one's. The redeploy left Garage running, as an unchanged config does;
      the owner restarted it at 17:41:57Z. Read back 2026-10-20: Prometheus's `garage` target
      `up` throughout, its cluster healthy with one node; ports 3900 and 3903 open on the
      NAS's management address and 3901 refused, as before; all 16 CNPG clusters (Whisparr
      v2's is gone) `ContinuousArchiving`, 10 of them archiving to it after the restart (the
      other six had no new WAL).
      The two failures in that window weren't Garage: a switchover in progress, and the
      Kubernetes API refusing during a node drain; both archived again within a minute. Left:
      Dockhand's deploy record showing success on #163's commit, since a deploy that never ran
      also leaves it running. semaphore sixth (#172):
      `DOCKER_VM_1_MGMT_ADDRESS`, `BASE_DOMAIN` (its web root, OIDC issuer and NetBox URL are
      built in the compose file), `DATABASUS_SEMAPHORE_DB_PASSWORD`, `NETBOX_SEMAPHORE_TOKEN`,
      `TIMEZONE`, `kea_default` written in, and seven that keep their names, which go from
      Dockhand just before the merge. `SEMAPHORE_OIDC_CLIENT_SECRET` stayed in Dockhand, its
      `.env` line blank, while Key Vault held it base64. Rendered with Key Vault's values and
      that one from Dockhand, its compose file gave exactly the old one's. Before: `semaphore`
      and `semaphore-postgres` running since
      2026-10-18 23:06Z, port 3001 open on the management address and refused on the
      production one, `/api/ping` answering `pong` through Traefik. The owner deleted the seven
      before the merge (21:10:47Z); Flux's alert called its webhook at 21:11:52Z. Read back
      2026-10-20: neither container recreated, as with garage and unlike tailscale and
      databasus; `/api/ping` answering `pong`, port 3001 open on the management address and
      refused on the production one, as before. The deploy ran: on the host (the owner,
      `docker ps -a --filter label=com.docker.compose.project=semaphore`), both init containers
      exited (0) at about 21:12Z, which each deploy re-runs, while `semaphore` and
      `semaphore-postgres` stayed up from 10-18. An unresolved reference would have changed
      their config and recreated them. Its OIDC client secret followed (#194), plain since
      #173: the compose file reads `AUTHENTIK_SEMAPHORE_OIDC_CLIENT_SECRET` by reference, Key
      Vault's value equal to Dockhand's by hash (the copy script's dry run, 2026-10-21: 52 held
      the same, none base64, none to add); rendered as compose does, the file gave exactly the
      old one's. No Dockhand variable has the new name, so nothing went before the merge; the
      old one is on the delete list. Merged 02:05:21Z; Flux dispatched the webhook event at
      02:05:36Z, with no error. Read back 2026-10-21: neither container recreated (both still
      from 10-18 23:06Z), `/api/ping` answering `pong` through Traefik, port 3001 open on the
      management address and refused on the production one, as before. cAdvisor never sees
      the init containers, which exit in seconds. The owner, 2026-10-21: Dockhand's log line
      (`docker logs --since 1h dockhand 2>&1 | grep 'reference(s) (promoted'`) shows the deploy
      resolved its references, and a login through Authentik works, which tests the client
      secret itself. semaphore is done. netbox
      seventh (#184): `DOCKER_VM_1_MGMT_ADDRESS`, `BASE_DOMAIN` (its
      host and OIDC endpoint are built in the compose file),
      `AUTHENTIK_NETBOX_OIDC_CLIENT_SECRET` by reference, `DATABASUS_NETBOX_DB_PASSWORD`,
      `TIMEZONE`, `kea_default` and its three sync settings written in (`SYNC_APPLY` true by
      hash, the two Dockhand never set at their defaults), and eight that keep their names,
      which go from Dockhand just before the merge. Rendered as compose does (parse, then
      interpolate), its compose file gave exactly the old one's. Its two images are built on
      each deploy, so a rebuilt image can recreate it whatever the values. Before: `netbox`
      and `netbox-dns-sync` running since 2026-10-20 03:26Z, the rest since 10-19 03:35Z,
      port 8085 open on the management address and refused on the production one, `/login/`
      answering 200 through Traefik. Read back 2026-10-21 (merged 00:39:59Z): `netbox` and
      `netbox-worker` not recreated; `netbox-dns-sync` recreated at 00:41:17Z, its image being
      built at each deploy (both Pi-hole URLs are `http`, so `PIHOLE_TLS_VERIFY` changes
      nothing); `/login/` answering 200, port 8085 open on the management address and refused
      on the production one, as before. Left, on the host: the init containers exited (0) at
      about 00:41Z (`docker ps -a --filter label=com.docker.compose.project=netbox`);
      `netbox-dns-sync` healthy; a login through Authentik. Dockhand held no `SYNC_DELETE` or
      `PIHOLE_TLS_VERIFY` (the listing of every variable, 2026-10-21), so the defaults the move
      assumed were what it ran with. kea eighth
      (#185): `DOCKER_VM_1_PROD_ADDRESS` and `DOCKER_VM_1_MGMT_ADDRESS`
      for its DHCP and API addresses, `KEA_POSTGRES_PASSWORD`, `DATABASUS_KEA_DB_PASSWORD`,
      `BASE_DOMAIN` (Stork's OIDC issuer and redirect are built in the compose file) and
      `AUTHENTIK_STORK_OIDC_CLIENT_SECRET` by reference; `KEA_DB_PASSWORD` and
      `STORK_DB_PASSWORD` keep their names, so they go from Dockhand just before the merge.
      Rendered as compose does, its compose file gave exactly the old one's. Three of its
      services run an image built at each deploy. Before: `kea-dhcp4` running since
      2026-10-18 13:48Z, `kea-postgres` since 10-17 11:29Z, `stork-server` and the Stork agent
      since 10-20 03:08Z; ports 8000, 8080 and 9547 open on the management address and refused
      on the production one; the HA pair in hot-standby, the standby in touch; Stork
      answering 200 through Traefik. The owner deleted the two before the merge (00:49:06Z);
      Flux called its webhook at 00:49:58Z. Read back 2026-10-21: `kea-dhcp4` and
      `kea-postgres` not recreated (Kea's uptime still 59 hours, so DHCP never stopped);
      `stork-server` and the Stork agent recreated at 00:50Z by their image's rebuild (the
      agent reads no variable) and running since; ports 8000, 8080 and 9547 open on the
      management address and refused on the production one, the HA pair in hot-standby with
      the standby heard 5 s before, Stork answering 200, Kea's metrics target `up`. Left, on
      the host: the init containers exited (0) at about 00:50Z; a Stork login through
      Authentik. vaultwarden ninth and last
      (#188): `DOCKER_VM_1_PROD_ADDRESS`, `BASE_DOMAIN` (its
      address, signups whitelist, SMTP host and SSO authority are built in the compose file),
      `VAULTWARDEN_ADMIN_TOKEN`, `OWNER_EMAIL`, `ENTRA_VAULTWARDEN_SMTP_*`,
      `AUTHENTIK_VAULTWARDEN_OIDC_CLIENT_*`, `GARAGE_S3_ENDPOINT` and
      `VAULTWARDEN_BACKUP_S3_*`; no variable keeps its name, so nothing goes from Dockhand
      before the merge. `BACKUP_ZIP_PASSWORD` stayed blank; it's now
      `VAULTWARDEN_BACKUP_ZIP_PASSWORD` (`QUEUE.md`). Dockhand hands resolved references to
      compose as its environment (`stacks.ts`: they join the secret variables,
      `Object.assign(spawnEnv, secretVars)`), so the argon2 admin token's `$`s
      arrive as they do now. Rendered as compose does, its compose file gave exactly the old
      one's. Before: `vaultwarden` running since 2026-10-16 17:35Z, `vaultwarden-backup` since
      10-20 08:03Z; port 443 open on the production address and refused on the management
      one; `/alive` answering 200 through Traefik and direct; `bitwarden-cli` ready, no
      restarts, and the four ClusterSecretStores Valid. Merged 01:16:43Z; Flux called its
      webhook at 01:17:24Z. Read back 2026-10-21: neither container recreated; port 443 open on
      the production address and refused on the management one, `/alive` answering 200
      through Traefik and direct, `bitwarden-cli` ready with no restarts and the four stores
      Valid, as before. The deploy ran: Dockhand's log line for it (the owner, `docker logs
      --since 1h dockhand 2>&1 | grep 'reference(s) (promoted'`) resolved 11 of 11 references
      from Key Vault, so an unchanged config, not a skipped deploy, left both running. Left: a
      login through Authentik and with the master password
- [ ] New values plain too (#189, merged 2026-10-21), checked live only by the next Secret
      promoted: its row in `kubernetes/.local/vaultwarden-pending.yaml` plain, with its
      `template` entry, and its manifest written once the value is in Key Vault and mapped
      with that entry. A row still base64 for a value that is text means capture's
      `secret_data` mark didn't reach `varsub.pending_rows`
- [ ] Nothing held base64 (#193, merged 2026-10-21), checked live only by
      `scripts/keyvault-secrets.ps1`'s first real runs, since it was proved on a mock vault and
      a fake kubectl: a listing with no `NEEDS b64enc`, then a set read back by hash and synced
- [ ] A promoted Secret's value that isn't text has no supported form: Key Vault holds only
      text, and the structure test refuses a Secret's variable without its template entry
      (capture's pending note says so). None exists today. One route, untried:
      Key Vault holds its base64, and its entry adds `decodingStrategy: Base64` to `remoteRef`,
      so the usual template line gives the Secret that base64 back. The structure test's
      `remoteRef == {key: ...}` check, and `keyvault-secrets.ps1`'s listing, would need to
      allow it
- [ ] The standard moved from Settled into `scripts/README.md`'s "Add or change a substitution
      variable", where a new name is chosen, once the first names follow it

## Left to delete when the move is done

The owner deletes these once every stack and manifest reads its standard name. Nothing reads
any of them now unless its line says so. A deleted Key Vault name stays reserved for 90 days
(purge protection), so only what is on this list goes. Each step adds what it leaves behind.

**Key Vault secrets:** none left. The owner deleted the ten old names on 2026-10-21. Read back:
all ten soft-deleted (kept 90 days by purge protection) and none active, `cluster-secrets`
refreshed after and `SecretSynced`, and every ExternalSecret Ready.

**Dockhand stack variables:** the listing of every one at 22:40 EDT on 2026-10-20, after the
queue had recorded them all deleted, still holds three that nothing reads:

- semaphore's `TZ` (now `TIMEZONE`, #172), vaultwarden's `VAULTWARDEN_BIND_ADDRESS` (now
  `DOCKER_VM_1_PROD_ADDRESS`, #188) and `BACKUP_ZIP_PASSWORD` (now
  `VAULTWARDEN_BACKUP_ZIP_PASSWORD`, #195; another value, never deployed)

It holds three more that stay: cadvisor's `DOCKER_VM_1_MGMT_ADDRESS` and kea-standby's
`DOCKER_VM_2_MGMT_ADDRESS` and `DOCKER_VM_2_PROD_ADDRESS`, the same references as their `.env`s.
Dockhand fills a stack's variables from its `.env` on its own (its env editor,
`previewRepoEnvFiles` in its `git.ts`), so they come back after a delete. Each `.env` checked
2026-10-21: every variable its compose file reads, as its own `azurekv://` reference to a secret
Key Vault holds, enabled; blank only claude-code's `REPO_URL` (its default is this repo) and
netbox's `CONFIG_DIFF_*` (unused)

**Files:**

- The all-variables listing, `dockhand_variables.json` in Downloads and
  `~/dockhand_variables.json` on the Docker host: names and SHA-256s, and a short password's
  hash can be guessed. Delete both

## Resuming

`scripts/keyvault-secrets.ps1` lists each cluster variable with its Key Vault name and
encoding (`| Where-Object Encoding -eq 'NEEDS b64enc'` gives any lacking its template entry); a
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
