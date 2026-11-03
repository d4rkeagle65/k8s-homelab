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
- [ ] **3. Every derived value from NetBox** (`scripts/keyvault_facts.py`, a Semaphore Python
  template), the owner's ask (2026-11-03): each value made from NetBox by one mechanism that
  grows by adding to it, so a change in NetBox (another prefix, address or record) reaches every
  value built on it at the next run with no edit anywhere else.
  - **How a value is made** (`scripts/keyvault_facts.yaml`, one line per Key Vault name):
    `take:` one form (`address`, `cidr`, `dns_name`, `range`) of the one object claiming the name
    in NetBox's custom field `keyvault_secrets`; `collect:` the same from every claimant, joined;
    `template:` text on other names; `view:` a Python file in `scripts/keyvault_views/` building
    a value with structure (a JSON variable) from NetBox through `site`. A JSON variable is
    compared as data and a difference reported as JSON paths. No host name or address is in git:
    an object is chosen by its claim, a VLAN's name, a role or a tag.
  - **Status:** only from an object NetBox's status table lets be used (README.md, "What a NetBox
    status means"); anything else is reported and its Key Vault value left alone. A name no object
    claims, or two claim for `take:`, fails the run.
  - **The runs:** `--find` (which secrets equal or contain which object; the evidence for tagging),
    the plain run (agree or differ, by name), `--write` (set what differs, read back), `--roles`
    (the writer's `az role assignment` lines). A value is never printed.
  - **Identity:** reads with the sync's reader (`semaphore-keyvault-reader`); writes with a service
    principal of its own, `Key Vault Secrets Officer` on each mapped secret, never the vault (the
    owner's). A new name needs its map line and its role assignment once; more objects under a
    name need neither.
  - **A new Key Vault name is new only when nothing derives it:** a URL or list built from facts is a
    `template:` or `collect:`, and a JSON variable a `view:`; a secret in one is its own Key Vault
    secret, named for its user, which the view reads with `site.secret`.
  - [x] Step 1 (#530): the script, its map empty, tests, the "Key Vault Facts" group.
  - [x] Step 2 (#531): `collect:` and `view:`, the `Site` a view reads through, JSON compared by
    path; template 41 ("Sync: NetBox → Key Vault (script)", `--find`, groups 14 and 16; task 1518:
    30 of 250 secrets match NetBox objects).
  - [ ] Step 3: the custom field `keyvault_secrets` (#5, multiple selection, choice set #2 "Key
    Vault secrets") on addresses, ranges, prefixes, devices and VMs; 22 objects tagged and read
    back, 23 names mapped (this PR, with `take: name` for devices and VMs). Left:
    - the plain run on template 41 (arguments overridden to none) agreeing for all 23;
    - `WORKER-NODE-4`–`7`: their VMs tagged (`take: name`), from a `--find` run that matches names;
    - `NAS-HOSTNAME`, `NFS-SERVER`, `PVE-API-HOST`: NetBox doesn't hold them (no address or DNS
      name matches); the owner says what each is (a name on which network) so NetBox records it;
    - the templates (the two Pi-hole URLs, Garage's S3 endpoint, the MariaDB backup host: each
      contains an address `--find` found) and `KEA-DB-CLIENTS` as a collect, their shapes asked of
      the owner by class, never value.
    `CLUSTER_DOMAIN` and the pod CIDR are the cluster's own (the Talos config in git).
  - [ ] Step 4: the JSON variables as views, one per PR, each until its plain run agrees:
    `TOFU-PROXMOX-TF-VAR-SITE` (networks from prefixes by VLAN, `netbox_seed.yaml`'s bridge
    list; its SSH keys their own secrets), `PIHOLE-SETTINGS` (dnsmasq lines from NetBox DNS's
    zones), `TOFU-CLOUDFLARE-TF-VAR-DNS` (Cloudflare's records as NetBox DNS records tagged for
    it, the record IDs in a custom field). Then Tailscale's routes, host name and tags.
  - [ ] Step 5: the owner makes the writer identity (`--roles` prints its assignments), its
    credentials in a group of their own ("Key Vault Facts Writer") and a second template that runs
    `--write` with it, on a schedule and before the jobs that read these values; template 41, which
    may have its arguments overridden, never gets the writer.

## Resume

Read the unchecked stage and the files it names. The owner has the export; ask for a value's
class (same as, contains, an address) rather than its value.

## Done when

No two Key Vault secrets hold the same value but for a stated reason, the cluster's own
passwords are made there, and every Key Vault value built on the site's facts (an address, a name,
a URL, a list, a JSON variable) is made from NetBox by the facts sync on a schedule.

## Checked, negative

- Semaphore's own Azure Key Vault storage: a stub in the open-source build
  (`QUEUE-semaphore-variables.md`).
