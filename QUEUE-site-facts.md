# QUEUE-site-facts: each value in Key Vault from one source, generated or derived where it can be

## The ask

The owner, 2026-10-31, from an export of every Key Vault secret (kept out of git: only names
appear here): many hold the same value under several names, many are addresses and names
NetBox, Kea, Pi-hole or the cluster already know, some passwords should be made in the
cluster, and three JSON strings bundle values that are their own facts.

## Settled

- **The naming rule** (`QUEUE-semaphore-variables.md`, "Settled"): one secret per value; a
  shared value has a generic name, a credential for one consumer is named for it; a URL that
  is `<name>.<domain>` is a template on `BASE-DOMAIN`, never a secret of its own.
- **NetBox is the source of the site's facts**: addresses, host names, prefixes, VIPs and pools.
  A value derived from it is written to Key Vault by a job, never typed, so Flux
  (`cluster-secrets`), Dockhand (`azurekv://`) and the Semaphore sync keep reading Key Vault
  unchanged. The job's identity may write only the names it owns (Key Vault RBAC scoped to each
  secret), and reads each write back.
- **A password nothing outside the cluster needs is generated in the cluster**: External
  Secrets' Password generator writes it to its Kubernetes Secret, so it's never in Key Vault,
  and each consumer has its own.
- **No value in git, a commit or a PR**: the export stays in the chat it was pasted into.

## Stages

- [x] **1. Duplicates and renames** (#503, #505, 2026-10-31): `OWNER-GMAIL` for the Gmail logins,
  `OWNER-USERNAME` for the owner's, `M365-RELAY-NOREPLY-EMAIL-ADDRESS` for Vaultwarden's sender,
  the ACME email as `{OWNER-EMAIL}`; Grafana's and qBittorrent's "admin" in cluster-settings, the
  TerraMaster's in Termix's ExternalSecret template. The replaced secrets and the import's 20
  unread copies deleted by the owner (`keyvault-secrets.ps1 -Delete -Yes`, #509); afterwards
  every ExternalSecret ready and the Semaphore sync complete (task 1491). Kept, each for a reason:
  `OBSIDIAN-COUCHDB-ADMINUSERNAME` (its base64 is in .secret-words) and the two Databasus
  passwords (made to differ at the next rotation).
- [ ] **2. Passwords generated in the cluster.** Traced 2026-10-31 (live pod specs, values compared
  yes/no only):
  - [x] Authentik's and Immich's unused `redis-password` Secrets (#511), live and in Key Vault.
  - [ ] redis-ha (this PR, merged when the owner picks; it restarts redis-ha and Immich): a
    Password generator makes `redis-ha-auth` (`auth`, `sentinel-password`, each its own,
    `refreshPolicy: CreatedOnce`), and Immich's `immich-redis` builds its `REDIS_URL` from it
    through a Kubernetes-provider SecretStore that may read only that Secret. After merge: check
    both ExternalSecrets ready, the two passwords differ (yes/no), redis-ha has one master and two
    replicas linked, and Immich's server and machine learning run on the new Secret (its jobs page:
    re-run "missing" if a queue was lost in the roll). Then by hand the old prune-protected
    `redis-ha/redis-ha-creds` and `immich/immich-redis-url` (capture would write the second back
    into `immich-extras` while it is live); then a PR removing `REDIS_HA_CREDS_AUTH`,
    `REDIS_HA_CREDS_SENTINEL_PASSWORD` and `IMMICH_REDIS_URL_REDIS_URL` from cluster-secrets; then
    the owner deletes those three from Key Vault.
  - [ ] Then the app secret keys in Key Vault that nothing outside the cluster reads.
- [ ] **3. The facts sync**: a map in git (Key Vault name: its NetBox source, or a template on
  others), a scheduled Semaphore job writing what differs and reading it back, its identity
  scoped to those names. First the addresses and names NetBox holds (the Docker VMs', NAS's,
  Pi-holes', switches', access points', the picture frame's, the Proxmox API host, the worker
  nodes), then what's derived from them (the Pi-hole and Garage URLs, `KEA_DB_CLIENTS`, the
  Kubernetes API URL), then the cluster's VIPs and MetalLB pool (NetBox records them from the
  cluster already) and Tailscale's routes, host name and tags. `CLUSTER_DOMAIN` and the pod
  CIDR are the cluster's own (the Talos config in git), so they come from there.
- [ ] **4. The JSON strings, one at a time**: `TOFU-PROXMOX-TF-VAR-SITE` (the Proxmox module
  reads its site facts from NetBox at plan time, as it already does for guests),
  `PIHOLE-SETTINGS` (its dnsmasq lines from NetBox DNS's zones), `TOFU-CLOUDFLARE-TF-VAR-DNS`
  (Cloudflare's records as NetBox DNS records tagged for it). An SSH key in one becomes its own
  secret, named for its user.

## Resume

Read the unchecked stage and the files it names. The owner has the export; ask for a value's
class (same as, contains, an address) rather than its value.

## Done when

No two Key Vault secrets hold the same value but for a stated reason, every address and name is
NetBox's, and the cluster's own passwords are made there.

## Checked, negative

- Semaphore's own Azure Key Vault storage: a stub in the open-source build
  (`QUEUE-semaphore-variables.md`).
