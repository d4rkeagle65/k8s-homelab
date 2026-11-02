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
  Secrets' Password generator writes it once (`refreshPolicy: CreatedOnce`) to a Secret of its
  own with a new name, so pods roll onto it and nothing adopts a live Secret. Except a key whose
  loss makes restored data unreadable (Authentik's, n8n's and Kept's keys, Organizr's hash key,
  Kept's backup password) or that an operator won't apply when it changes (the MariaDB root
  passwords: the operator logs in as root, and a restored backup keeps the old one): a
  generated value dies with the cluster, so those stay in Key Vault.
  So do values something outside the cluster holds, passwords the owner types, and API keys an
  app issues itself. A value whose consumers must change together (redis-ha's) restarts them all
  at once, never one pod at a time.
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
- [x] **2. Passwords generated in the cluster** (#511–#528, 2026-11-02): redis-ha's, then three
  batches (n8n, Zammad, Obsidian, Homepage, Linkwarden; the six in-cluster OIDC client secrets and
  Kept's metrics token; Kept's, Grimmory's and Youtarr's database logins), and Immich's superuser
  left to CloudNativePG. The Key Vault secrets they replaced are deleted.
- [ ] **3. The facts sync** (`scripts/keyvault_facts.py`, a Semaphore Python template).
  - **How a Key Vault name finds its NetBox object:** a NetBox custom field `keyvault_secrets`
    (multiple selection, its choices the map's names) on IP addresses, IP ranges and prefixes. The
    object says which secrets it feeds, so no host name or address is in git. The map in git
    (`scripts/keyvault_facts.yaml`) says only what each name takes from its object (`address`, the
    host part; `cidr`; `dns_name`; `range`, `start-end`) or a template on other names
    (`{PIHOLE1-MGMT-ADDRESS}`).
  - **Status:** written only from an object NetBox's status table lets be used (README.md, "What a
    NetBox status means"): the address Active, Reserved or DHCP and its device or VM Staged or
    Active. Anything else, a name no object claims, or one two claim, is reported and its Key
    Vault value left alone.
  - **The runs:** `--find` compares every Key Vault secret with every NetBox address, range and
    prefix in each form and reports which equal which (`NAS-HOSTNAME: dns_name of IP address
    #123`); that evidence says what to tag and how each name maps, with no value seen. The plain
    run reports, per mapped name, whether NetBox and Key Vault agree. `--write` sets what differs
    and reads it back. A value is never printed.
  - **Identity:** reads with the sync's reader (`semaphore-keyvault-reader`); writes with a service
    principal of its own, `Key Vault Secrets Officer` assigned on each mapped secret, never the
    vault (the owner's: the script prints the `az role assignment` lines from the map).
  - [ ] Step 1 (this PR): the script (`--find`, the plain run, `--write`, `--roles`), its map
    empty, tests, and the "Key Vault Facts" group (`NETBOX_URL`, `NETBOX_TOKEN` from
    `NETBOX-READ-TOKEN`). Its NetBox half checked against the live NetBox (144 objects, read
    only). Left, the owner's: the template "Sync: NetBox → Key Vault (script)" (Python,
    `scripts/keyvault_facts.py`, no inventory, both "Key Vault Sync" and "Key Vault Facts", the
    argument `--find`); then I run it and read its report.
  - [ ] Step 2: the custom field and its choices (mine, through NetBox's API); each object tagged
    from `--find`'s evidence; the map filled in.
  - [ ] Step 3: plain runs agreeing for every name; the owner makes the writer identity; `--write`
    on a schedule (the owner's).
  - First the addresses and names NetBox holds (the Docker VMs', NAS's, Pi-holes', switches',
    access points', the picture frame's, the Proxmox API host, the worker nodes, the VIPs and
    MetalLB pool), then what's derived from them (the Pi-hole and Garage URLs, `KEA_DB_CLIENTS`).
    `CLUSTER_DOMAIN` and the pod CIDR are the cluster's own (the Talos config in git); Tailscale's
    routes, host name and tags come later.
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
