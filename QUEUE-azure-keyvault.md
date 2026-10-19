# The cluster's and Dockhand's secrets from Azure Key Vault

**The ask (2026-10-19):** move the `cluster-secrets` variables from Vaultwarden (read through the
`bitwarden-cli` pod) to Azure Key Vault, so Dockhand's stacks can read the same store; Dockhand
supports Key Vault as a secret provider since v1.0.44 (bulk pulls, or inline `azurekv://`
references).

## Settled

- **One Key Vault secret per variable** (the owner, 2026-10-19), not one JSON secret read with
  `dataFrom`: Dockhand reads Key Vault one secret at a time. The `cluster-secrets` Secret, its
  name and every `${VAR}` stay as they are; only the ExternalSecret's source changes.
- **Names: the variable with `_` replaced by `-`** (`MEDIA_SONARR_API_KEY` is the Key Vault
  secret `MEDIA-SONARR-API-KEY`). Key Vault takes only letters, digits and `-`, 1-127
  characters, and ignores case. Checked 2026-10-19: the 141 variables map to 141 distinct
  names, none invalid, the longest 45 characters; `Child1`/`Child2` are the only mixed-case
  ones. The ExternalSecret maps `secretKey: MEDIA_SONARR_API_KEY` from
  `remoteRef.key: MEDIA-SONARR-API-KEY`.
- **Values move exactly as Vaultwarden holds them**: a field that is base64 there (used in a
  Secret's `data:`) is base64 in Key Vault too, so nothing that uses it changes.
- **Cost, measured 2026-10-18:** External Secrets reads once per variable per refresh, so
  `cluster-secrets` (141 entries, every 15 minutes) makes about 14,500 reads a day, about
  440,000 a month: about $1.30 a month at Standard's $0.03 per 10,000 operations (third-party
  figure; check the Azure calculator). `externalsecret_provider_api_calls_count` in Prometheus
  (from #103) has the rate from then on.
- **I never write a secret value**: the copy into Key Vault is a script the owner runs, which
  prints names and match/no-match only.
- **Who reaches the vault** (the owner, 2026-10-19; each role scoped to this vault, each service
  principal proved to sign in and list over REST): the owner, "Key Vault Secrets Officer";
  `k8s-homelab-external-secrets`, the cluster's, "Key Vault Secrets User" (read only);
  `k8s-homelab-claude-code`, the Docker VM's Claude Code, "Key Vault Secrets Officer" (read and
  write); and Dockhand's own principal, "Key Vault Secrets Officer". The two new principals'
  credentials are in `kubernetes/.local/azure-keyvault-sp-external-secrets.json` and
  `azure-keyvault-sp-claude-code.json` (fields `appId`, `password`, `tenant`), with two-year
  client secrets, to about 2028-10.

## Waiting on the owner

1. **How a Dockhand bulk pull names its variables** (does `MEDIA-SONARR-API-KEY` arrive as
   `MEDIA_SONARR_API_KEY`?): from Dockhand's manual or UI. Its principal holds "Key Vault
   Secrets Officer" (read and write); if Dockhand only reads, "Key Vault Secrets User" is
   enough. Its client secret expires 2028-10-05.
2. **Vaultwarden afterwards:** stays the password manager for people; it stops being the
   cluster's store once the cutover has run a week.

## Rules

- **A store is proved before it is used:** a test ExternalSecret into a scratch Secret, every
  key compared with `cluster-secrets` by hash, before the real one points at Key Vault.
- **Cutover in one commit, read back:** `cluster-secrets.yaml`'s store and every `remoteRef`
  change together; then `SecretSynced`, every key's hash unchanged, and every Flux
  Kustomization Ready. The way back is reverting that commit; the `bitwarden-*` stores stay
  until the last box.
- **A new variable afterwards:** its Key Vault secret first, then its entry in
  `cluster-secrets.yaml` (`CLAUDE.md`'s rule, with Key Vault in place of Vaultwarden).
- **The vault's address and sign-in come from a bootstrap Secret, never from `cluster-secrets`**,
  which the vault itself builds: a rebuilt cluster would wait on itself. The vault URL, tenant
  and the service principal's ID and secret go in one Secret applied by hand (kept in
  `kubernetes/.local/`), which the `ClusterSecretStore` and the `cluster-secrets` Flux
  Kustomization's substitution read.
- **The cluster then needs the internet to build `cluster-secrets`**: a rebuild with no route to
  Azure has no `INGRESS_IP` and so no Traefik. Today's Secret survives an outage (External
  Secrets keeps the last values); only changes wait.

## Checklist

- [ ] The owner's two answers above
- [x] The vault (the owner, by hand, before 2026-10-19), read 2026-10-19 with the Azure CLI:
      Standard, East US, Azure RBAC, soft delete (90 days) and purge protection on, public
      network access with no firewall rules (sign-in required, so the cluster and the Docker
      VM reach it as they are), no secrets readable to the owner yet
- [x] The owner's, the cluster's and Claude Code's access (2026-10-19, "Who reaches the vault"
      above), read back: each role at the vault, both credential files complete, each new
      principal signing in and listing (HTTP 200, the vault still empty)
- [ ] The Docker VM's Claude Code given its credential through Dockhand (a secret stack
      variable), then checked from inside the container
- [x] The copy script, `scripts/keyvault_copy.py`: the 141 mapped variables from the live
      `cluster-secrets` Secret into Key Vault under their mapped names, read back by hash; a dry
      run by default (2026-10-19: 141 missing, nothing written)
- [x] The owner's run of it with `--apply` (2026-10-19). Read back afterwards: a dry run
      reports all 141 "already the same" by hash, and the vault holds exactly the 141 mapped
      names. A variable changed in Vaultwarden before the cutover reaches Key Vault by running
      it again.
- [x] The `azure-keyvault` ClusterSecretStore (`prod/system/external-secrets/azure-keyvault/`,
      its Kustomization substituting the vault's URL and tenant from `azure-keyvault-bootstrap`)
      and the test ExternalSecret `cluster-secrets-keyvault-test` (`kubernetes/secrets/`, every
      vault secret by `find`, hourly), written and dry-run server-side 2026-10-19
- [x] The bootstrap Secret `azure-keyvault-bootstrap` in `flux-system` (keys
      `AZURE_KEYVAULT_URL`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`),
      applied by the owner 2026-10-19 from
      `kubernetes/.local/azure-keyvault-sp-external-secrets.json`; read back: the four keys
      match the file, and they sign in and list all 141 secrets. A rebuilt cluster needs it
      applied again before the `azure-keyvault` Kustomization can build its store.
- [x] Read back after the merge (#110, 2026-10-19): the store Valid, the test ExternalSecret
      `SecretSynced`, and all 141 of `cluster-secrets`' variables equal by hash to their Key
      Vault copies, none missing or extra
- [x] The cutover commit, written 2026-10-19: `cluster-secrets.yaml` on the `azure-keyvault`
      store, each entry reading its mapped name; the test ExternalSecret gone; the variable
      test checks the store and every mapped name; `CLAUDE.md`, `kubernetes/CLAUDE.md`,
      `docker/CLAUDE.md` and `scripts/README.md` say Key Vault. The owner merges it.
- [x] Read back after the cutover (#112, 2026-10-19): `cluster-secrets` `SecretSynced` from
      `azure-keyvault` for its new spec; all 142 keys (141 variables and the template's one)
      equal by hash to a fingerprint taken before; every Kustomization and HelmRelease Ready;
      External Secrets' calls now `GetSecret` on Azure Key Vault, its `bitwarden-cli` request
      count flat; `scripts/keyvault_copy.py`'s dry run "141 already the same"
- [ ] Dockhand's stacks on Key Vault (`docker/`), one stack first
- [ ] Tooling: `scripts/vaultwarden-fields.ps1` against Key Vault (from the cutover it no
      longer works on `cluster-secrets`: it reads the Vaultwarden item ID from the live
      ExternalSecret, which then names Key Vault secrets); capture's "aren't in Vaultwarden
      yet" warning and `vaultwarden-pending.yaml`'s name (`scripts/k8s_backup/varsub.py`)
- [ ] An alert on an ExternalSecret that stops syncing (`externalsecret_status_condition`)
- [ ] A week on Key Vault, then `bitwarden-cli` and the three `bitwarden-*` stores retired

## Resuming

`kubectl get clustersecretstores` and the `cluster-secrets` ExternalSecret's `secretStoreRef`
show which store is live; the first unticked box is next.

## Done when

Every box is ticked; then this file moves to `old-queues/`.

## Checked and negative

- **One JSON secret read by `dataFrom` (about 96 reads a day, under a cent a month)**: cheaper,
  but Dockhand reads Key Vault secret by secret; not taken.
