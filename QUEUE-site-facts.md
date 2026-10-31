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

- [ ] **1. Duplicates and renames** (the quick wins). In two PRs, since a new cluster-secrets
  variable reaches the cluster before what uses it and an entry reads only its own name: the
  new names, the Semaphore config and the Docker rename (this PR, after the owner adds
  `OWNER-GMAIL`, `OWNER-USERNAME` and `M365-RELAY-NOREPLY-EMAIL-ADDRESS`); then the cluster's
  consumers and the old entries (#503 merged 2026-10-31; this PR the second: the Gmail and
  username consumers on OWNER_GMAIL and OWNER_USERNAME, Termix's admin on OWNER-USERNAME; the
  apps' own "admin" logins in cluster-settings, public, under their names: GRAFANA_ADMIN_USER and
  QBITTORRENT_USERNAME, and the TerraMaster's in Termix's ExternalSecret template. Obsidian's
  CouchDB login stays in Key Vault: its Secret reads it in data:, as base64, and that base64 is
  in .secret-words); then the owner deletes the old secrets
  (`keyvault-secrets.ps1 -Delete`, which refuses while anything reads one). Each is a mapping change where the old
  name is read (`kubernetes/secrets/cluster-secrets.yaml`, an app's ExternalSecret, a Docker
  stack's `.env`, `scripts/semaphore_variable_groups.yaml`), the new secret made first by the
  owner (`scripts/keyvault-secrets.ps1`), then the old deleted once nothing reads it:
  - `SEMAPHORE-KEYVAULT-TENANT-ID` duplicates `ENTRA-TENANT-ID`: deleted (a safekeeping copy).
  - `EUFY-HOMEASSISTANT-EMAIL`, `HA-IOT-TPLINK-USERNAME`, `WYZE-EMAIL`: one `OWNER-GMAIL`.
  - `GRIMMORY-ADMIN-USERNAME`, `ORGANIZR-ADMIN-USERNAME`, `TERMIX-SSH-ADMIN-USER`,
    `YOUTARR-ADMIN-USERNAME`, `DEBIAN-ADMIN-USER`: one owner's username secret.
  - `GRAFANA-ADMIN-USER`, `QBITTORRENT-USERNAME`, `TERMIX-SSH-TERRAMASTER-USER`: the literal
    name in git (it names nothing of the site), their secrets deleted.
    `OBSIDIAN-COUCHDB-ADMINUSERNAME` stays: its base64 form is in .secret-words.
  - `M365-SHARED-MAILBOX-ADDRESS` renamed `M365-RELAY-NOREPLY-EMAIL-ADDRESS`.
  - `PROXMOX-SETTINGS-ACME-EMAIL`: the template `{OWNER-EMAIL}`.
  - Not merged: `DATABASUS-KEA-DB-PASSWORD` and `DATABASUS-SEMAPHORE-DB-PASSWORD` are one role on
    two databases, so each keeps its name; they're made to differ at the next rotation.
  - The import's copies the Semaphore config no longer reads (`QUEUE-semaphore-variables.md`),
    deleted once their groups are managed.
- [ ] **2. Passwords generated in the cluster**: Authentik's and Immich's Redis, and redis-ha's
  auth and Sentinel passwords (today two pairs sharing one value each), each from a Password
  generator into its own Secret; each switch restarts that Redis and its clients once. Then
  the app secret keys in Key Vault that nothing outside the cluster reads.
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
