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

## Waiting on the owner

1. **The owner's own access to the secrets:** the vault uses Azure RBAC, where the owner's
   inherited Owner role manages the vault but reads none of its secrets (listing is refused,
   "readMetadata", no assignment). The owner gives themselves "Key Vault Secrets Officer" on
   the vault before the copy script can run.
2. **How the cluster signs in:** recommended, a new service principal with a client secret and
   only "Key Vault Secrets User" on this vault, its credential kept like `bitwarden-cli`'s
   login today (`kubernetes/.local/`, the bootstrap Secret below). The owner creates it and
   its secret; workload identity federation would need the cluster's service-account issuer
   published on the internet.
3. **Dockhand's identity, and how a bulk pull names its variables** (does `MEDIA-SONARR-API-KEY`
   arrive as `MEDIA_SONARR_API_KEY`?): from Dockhand's manual or UI. Its service principal
   exists and holds "Key Vault Secrets Officer" (read and write); if Dockhand only reads,
   "Key Vault Secrets User" is enough. Its client secret expires 2028-10-05.
4. **Vaultwarden afterwards:** stays the password manager for people; it stops being the
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

- [ ] The owner's four answers above
- [x] The vault (the owner, by hand, before 2026-10-19), read 2026-10-19 with the Azure CLI:
      Standard, East US, Azure RBAC, soft delete (90 days) and purge protection on, public
      network access with no firewall rules (sign-in required, so the cluster and the Docker
      VM reach it as they are), no secrets readable to the owner yet
- [ ] The cluster's and Dockhand's identities on it, read-only ("Key Vault Secrets User" or an
      access policy allowing get and list), and the owner's write access
- [ ] A copy script (`scripts/`): every `cluster-secrets` field from Vaultwarden into Key Vault
      under its mapped name, then compared by hash; the owner runs it
- [ ] A `ClusterSecretStore` `azure-keyvault` (`azurekv` provider, the service principal's
      Secret) beside the `bitwarden-*` ones, and the test ExternalSecret proving all 141
- [ ] The cutover commit (the rules above); `test_every_substituted_variable_is_defined`'s
      "field name must match the variable" check becomes the mapped-name check
- [ ] Dockhand's stacks on Key Vault (`docker/`), one stack first
- [ ] Tooling: `scripts/vaultwarden-fields.ps1`'s list and set against Key Vault; the
      substitution-variable recipe in `scripts/README.md`; `CLAUDE.md`'s and
      `kubernetes/CLAUDE.md`'s Vaultwarden and `bitwarden-cli` rules
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
