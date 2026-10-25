# Kept in the cluster

The ask (2026-10-24): deploy Kept (github.com/keptinventory/kept, a household inventory) to the
cluster.

## Settled

- **Private only:** `kept${SUBDOMAIN_SUFFIX}.${BASE_DOMAIN}` on the `traefik` IngressClass,
  `websecure` and `traefik_private-networks@kubernetescrd`, never the tunnel.
- **Sign-in through Authentik:** an OIDC blueprint ("Kept", client id `kept`), its client
  secret `AUTHENTIK_KEPT_OIDC_CLIENT_SECRET`. Callback `<public URL>/api/v1/auth/callback/oidc`
  (Kept's `apps/server/src/auth/oidc.ts`). Kept links an Authentik login only to an existing or
  invited account, unless `KEPT_OIDC_AUTOPROVISION_GROUPS` names a group the login carries:
  members of the Authentik group "Kept Users" get an account on first sign-in.
- **Kept's own backups on, to Garage:** bucket `kept-backups`, key `kept`
  (`GARAGE_KEPT_S3_*`), restic password `KEPT_BACKUP_PASSWORD`. Beside, not instead of, the
  database's WAL and base backups to Garage that every CloudNativePG cluster has.
- **Upstream Helm chart** (`oci://ghcr.io/keptinventory/charts/kept`, its version is Kept's),
  in a `.handwritten` release, so Renovate proposes its updates. Upstream says the chart has
  not been installed on a cluster before: the first install is a test of it.
- **External Postgres, a CloudNativePG Cluster `kept-db`** on `local-db`, PostgreSQL 18
  standard image, which has `vector`, `pg_trgm` and `unaccent` (checked on mealie-db's).
  The chart's roles Job is off (`roles.create: false`): the Cluster makes Kept's four logins
  itself, as the chart's `files/roles.sh` would (owner, app, auth, system; the database and
  `public` owned by `kept_owner`; `kept_app`'s timeouts), each login's password from its
  `KEPT_DB_*_PASSWORD`. Passwords go into postgres:// URLs, so they are hex.
- **Keys from Key Vault** (`KEPT_SECRET_KEY`, `KEPT_AUTH_SECRET`: 32 random bytes, base64url),
  not generated into the config volume: losing that volume would make every stored secret
  unreadable.
- **Metrics:** `/metrics` with the bearer `KEPT_METRICS_TOKEN`, scraped by Prometheus.
- **Allow private addresses is on** (Kept's Admin > Sign-up > Private addresses, kept in its
  database, read at startup): Authentik's address resolves privately inside the house, and
  without it Kept turns OIDC off. A rebuilt Kept needs it set again, then a restart.
- **No mail yet:** `KEPT_SMTP_URL` unset, so invites are logged, not sent.

## Checklist

1. [x] The owner: the Garage bucket and key, and the 11 Key Vault secrets (below).
2. [x] Merge the `cluster-secrets` pull request, #371, only after
   step 1: a mapped secret missing from Key Vault fails the whole `cluster-secrets` sync.
   Check `./scripts/keyvault-secrets.ps1` shows all 11 Synced yes.
3. [x] Merge the deployment pull request, #373 (based on #371's commit): Namespace, `kept-db` with its ObjectStore and scheduled
   backup, Secrets, HelmRelease and its HelmRepository, the Authentik blueprint and its Secret
   and worker variable, the scrape config, the LimitRange. Its database URLs carry no sslmode
   (its body says why).
4. [ ] After its merge. Checked on 2026-10-24: kept-db healthy, archiving (`pg_stat_archiver`
   6 archived, 0 failed) and its first base backup completed; the four logins, the three
   extensions, the database owned by `kept_owner`, 163 tables migrated; `/readyz` 200 on
   1.12.5; `kept-tls` issued; Authentik's discovery for `kept` answers (the blueprint's first
   apply failed before the worker had the variable, then `ak apply_blueprint` applied it);
   Prometheus `up{job="kept"}` 1. Left, the owner's: the setup code
   (`kubectl -n kept logs deploy/kept | grep 'KEPT SETUP CODE'`) and the first account; joining
   "Kept Users" in Authentik and signing in with it; a first Kept backup in Garage (nightly at
   02:30 UTC, or Admin > Backups); the recovery kit
   (`kubectl -n kept exec deploy/kept -- kept admin recovery-kit`) kept off the cluster.
5. [ ] Homepage link, and a restore test of `kept-db` like the others.

## The owner's part (step 1)

On the NAS, as root:

```sh
g="docker exec garage /garage -c /etc/garage/garage.toml"
$g bucket create kept-backups
$g key create kept
$g bucket allow --read --write kept-backups --key kept
```

Then each secret with `./scripts/keyvault-secrets.ps1 NAME -New -Raw` (it asks for the value
without echoing it). The key's ID and secret from `key create` are
`GARAGE_KEPT_S3_ACCESS_KEY_ID` and `GARAGE_KEPT_S3_SECRET_ACCESS_KEY`. The rest are random,
made in PowerShell 7 and passed with `-Value` from a variable, so no value is printed:

```powershell
$hex = { [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)).ToLower() }
$key = { [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).TrimEnd('=').Replace('+','-').Replace('/','_') }
foreach ($n in 'KEPT_DB_OWNER_PASSWORD','KEPT_DB_APP_PASSWORD','KEPT_DB_AUTH_PASSWORD','KEPT_DB_SYSTEM_PASSWORD',
               'KEPT_BACKUP_PASSWORD','KEPT_METRICS_TOKEN','AUTHENTIK_KEPT_OIDC_CLIENT_SECRET') {
  $v = & $hex; ./scripts/keyvault-secrets.ps1 $n -New -Raw -Value $v }
foreach ($n in 'KEPT_SECRET_KEY','KEPT_AUTH_SECRET') {
  $v = & $key; ./scripts/keyvault-secrets.ps1 $n -New -Raw -Value $v }
Remove-Variable v
```

## Resume

Read this file and `git log --oneline -- kubernetes/prod/apps/kept QUEUE-kept.md`; the
checklist's first unticked step is next. Done when step 5 is ticked; then this file moves to
`old-queues/` with a row in its README.
