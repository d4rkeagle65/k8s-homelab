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
    the plain run (agree or differ, by name), `--write` (set what differs, read back). A value is never printed.
    The writer's role assignments: `keyvault-secrets.ps1 -FactsWriter` reports which mapped secrets
    it lacks, `-Apply` adds them (the map's `writer:` names it; `keyvault_facts.py --names` gives
    the list).
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
  - [x] Step 3 (#532–#534, #539): the custom field `keyvault_secrets` (#5, multiple selection, choice
    set #2) on addresses, ranges, prefixes, devices, VMs and NetBox DNS records; 31 objects claim
    37 names; every one agrees (task 1540). `ZAMMAD-ALERT-WEBHOOK-URL` carries a token and only
    zammad-bridge reads it, so it goes with the bridge (QUEUE-n8n-zammad.md, stage 5). A claim lost
    to a NetBox sync shows in a check run as "no NetBox object claims it". `CLUSTER_DOMAIN` and the
    pod CIDR are the cluster's own (the Talos config in git).
  - [ ] Step 4: the JSON variables as views, one per PR, each until its plain run agrees. `--find`
    reports each JSON variable's leaves by path: which NetBox form (or a prefix's network address or
    first three octets) or other Key Vault secret each equals, and the paths that match nothing; a
    view is written from that, never from the value (#541). A JSON key is printed only when it's a
    plain name, otherwise by position (#542; task 1542's log, from before it, the owner keeps).
    - [x] `TOFU-PROXMOX-TF-VAR-SITE` (#543): the view `tofu_proxmox_site`, agreeing (task 1543).
      Networks from each bridge's VLAN prefix (`netbox_seed.yaml`), a gateway from the DNS record
      tagged `gateway` in its prefix (#119), DNS servers from the Pi-hole VMs' Production addresses,
      the API from the record tagged `kubernetes-api` (#136); its own secrets `NODE-SEARCH-DOMAIN`,
      `KUBERNETES-SERVICE-CIDR`, `OWNER-SSH-PUBLIC-KEY`, `SEMAPHORE-SHARED-SSH-PUBLIC-KEY`. The
      writer holds all 38. The next write rewrites it compact (the same data) and names it
      "changed".
    - [x] The nodes' search domain (#560): the zone the Production prefix names; plan 1567 and
      apply 1568 changed one plain container's DNS domain and nothing else; `NODE-SEARCH-DOMAIN`
      deleted.
    - [x] `PIHOLE-SETTINGS` (#544; task 1544: differs only in the lines' order, as expected): the
      view `pihole_settings`, a `local=/<zone>/` line for each
      active NetBox DNS zone tagged `pihole-local` (#7, #8, #9, and the DMZ's and work laptops'
      #12 and #13, made for it, unlinked from their prefixes, since a prefix's `dns_zone` makes the
      NetBox sync name every address in it), sorted by name, then `filter-rr=HTTPS` and the NTP
      switches, which are configuration (in the view). The same lines as Key Vault's, in another
      order, so its first write is followed, at the next Pi-hole setting run, by one set and FTL
      restart per Pi-hole. Before that write: `keyvault-secrets.ps1 -FactsWriter -Apply` (39 names).
    - [x] Cloudflare's records from NetBox (#545, #547, #548; the owner's ask): `tofu/cloudflare`
      reads NetBox DNS's `public` view (#2), zones #14 and #15 tagged `cloudflare`, at every plan;
      local DNS and ExternalDNS keep to the default view. Plan 1548 and apply 1549: 29 moves, nothing
      else; plan 1551 and 1553: no changes. `TF_VAR_dns` and `TOFU-CLOUDFLARE-TF-VAR-DNS` are gone. A
      public zone's nameservers, SOA primary and contact are Cloudflare's (`dns.cloudflare.com`):
      copied from a local zone, NetBox DNS made NS and SOA records naming the Pi-holes.
    - [x] Tailscale (#549, #550): `TAILSCALE-ROUTES` the view `tailscale_routes` (the prefixes
      tagged `tailscale-route-home`, the tag tofu/tailscale's policy approves), `TAILSCALE-TAGS` in
      docker/tailscale's compose.yaml (the secret deleted), `TAILSCALE-HOSTNAME` Docker VM 1's
      NetBox name (VM #4 claims it). Redeployed 2026-11-03: the router reports the VM's name, online,
      its home routes and exit node enabled (the exporter's metrics).
    - [ ] `PROXMOX-SETTINGS-NTP-SERVE-NETWORKS` (#561; check 1569 agrees, untagged): the view `proxmox_ntp_serve_networks`,
      the prefixes tagged `ntp-clients` (the Management prefix, #1, tagged), equal to Key Vault's
      value. After merge, the owner's: `keyvault-secrets.ps1 PROXMOX-SETTINGS-NTP-SERVE-NETWORKS
      -FromNetBox` (it exists, so this gives the writer its role), then a write.
    - [x] `DEBIAN-ADMIN-SSH-KEYS` (#546): the Debian Host Baseline group builds `admin_ssh_keys` from
      `OWNER-SSH-PUBLIC-KEY` (a template with `json: true`; task 1546 rebuilt it); the old copy is
      deleted. The next Debian admins or baseline run rewrites each `authorized_keys` line once,
      the key's comment added.
    - [x] #545 live: ExternalDNS's NetBox pod runs it ("All records are already up to date"); the
      owner checks in Dockhand that netbox-dns-sync was rebuilt and its round is clean.
  - [x] Fingerprints (#535): a run lists Key Vault once and reads a value only where the keyed
    fingerprint tag (`keyvault-facts`, an HMAC with `KEYVAULT-FACTS-FINGERPRINT-KEY`) on its current
    version doesn't match NetBox's value; `--write` tags what it sets and an agreeing value it finds
    untagged (the tag only, the value untouched); `--full` reads every value; the last line names
    what changed, for the Key Vault sync's `--secrets`. The key exists; every value is tagged.
  - [x] The survey (#536): `FACTS_MODE` (`check`, `find`, `full`, `write`, `write-full`), an
    argument `FACTS_MODE=value` or an environment variable, in place of the flags. Template 41
    ("(read)") has it (`check`, `find`, `full`), no arguments, groups 14 and 16 only.
  - [x] Step 5, the writer: service principal `semaphore-keyvault-facts`, Key Vault Secrets Officer
    on each mapped secret only (`keyvault-secrets.ps1 -FactsWriter` / `NAME -FromNetBox`), its ID and
    secret in Key Vault, group 17 from the sync; template 42 ("(write)", survey `write`) runs daily
    at 03:30, before template 38's 04:00 sync; a failed run tickets through n8n.

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
