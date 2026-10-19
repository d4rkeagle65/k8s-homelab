# NetBox owns local DNS; the Pi-holes are copies

**The ask (the owner, 2026-10-19):** every record in Pi-hole goes into NetBox. ExternalDNS keeps
updating and managing its own records there; records the owner makes by hand in NetBox are
synced to the Pi-holes; and every address on each device and VM gets a record of its own, in a
zone that depends on the address's subnet (management, production, IoT, WAN). `QUEUE-netbox.md`,
phase 4.

## Settled (the owner, 2026-10-19)

- **Every Pi-hole record is imported once**; after that NetBox is the only place a local record
  is made. Imported records are hand-made ones as far as NetBox is concerned.
- **ExternalDNS writes only NetBox** (the `external-dns-netbox` release and its webhook, which
  owns the records described `external-dns`). Its two Pi-hole releases go once the sync carries
  their records.
- **Per-address records for devices and VMs**, in the management, production, IoT and WAN
  networks: the name is the object's NetBox name, the zone the one named on the address's
  prefix. The zones' names are real values, so they live in NetBox (each prefix's custom field
  `dns_zone`), never in git.
- **A script in this repo pushes NetBox to the Pi-holes**, not octoDNS (below, checked and
  negative), and it runs beside NetBox on the Docker VM, so the hosts' records don't depend on
  the cluster being up.

## Rules

- **One owner per record:**
  | Record | Owner | How it reaches NetBox |
  |---|---|---|
  | An Ingress hostname | ExternalDNS | the webhook, described `external-dns` |
  | A device's or VM's address | the address's DNS name, which `scripts/netbox_seed.py` sets from the prefix's `dns_zone` | NetBox DNS's DNSsync makes the record (`managed`) |
  | Anything else | the owner, by hand in NetBox | (imported once from Pi-hole 1) |
- **The seed owns an address's DNS name** when the address is on a device's or VM's interface
  and its prefix has a `dns_zone`: `<object name>.<zone>`, the name lower-cased with anything
  outside letters, digits and hyphens turned into hyphens. Every such address gets one, the
  primary and the rest; an address elsewhere keeps whatever it has.
- **The sync owns every name inside a NetBox zone on both Pi-holes, and nothing outside them.**
  It adds what NetBox has and Pi-hole lacks; it removes a Pi-hole record in a NetBox zone that
  NetBox doesn't have only when deletes are switched on (off in the sample config: a removal
  can't be undone from NetBox, which never had the record). It reads each change back.
- **A failed read is not an empty list.** NetBox not answering, or answering with no records,
  stops the run before anything is written; so does a Pi-hole that refuses a read.
- **Pi-hole keeps a name and its addresses as `<ip> <name> [<name>...]` lines** (`dns.hosts`);
  the sync writes one name per line, and an existing line with two names is replaced by one
  line each.

## Where things live

- **NetBox:** zones and records (NetBox DNS, the `_default_` view, its SOA naming both
  Pi-holes); the prefixes' `dns_zone`; the view's prefixes (DNSsync only acts on a prefix linked
  to a view).
- **The sync:** `docker/netbox/dns-sync/netbox_pihole_sync.py`, built into the
  `netbox-dns-sync` service of the `docker/netbox/` stack (Dockhand builds from the stack's own
  folder); tests in `scripts/tests/`. Its credentials are the stack's Dockhand variables: a read-only
  NetBox token, and each Pi-hole's URL and app password.
- **What goes:** the `external-dns` and `external-dns-pihole2` releases' Pi-hole writes;
  nebula-sync's copy of `dns.hosts` and `dns.cnameRecords` (its other DNS settings stay); the
  Pi-hole write in `ansible/tasks/proxmox-acme.yml` (its record becomes a NetBox one).

## Checklist

- [x] NetBox (2026-10-19, through its API, read back): the zones for management, production,
      IoT and WAN beside the base domain's, with its SOA and nameservers; a sixth for Pi-hole 1's
      one name under a reversed domain, kept as found for the owner to fix or drop;
      the WAN prefix; the custom field `dns_zone` on prefixes, set on those four; and the four
      prefixes linked to the `_default_` view
- [x] The seed's DNS-name pass (`wanted_dns_names` in `netbox_seed.py`, #86): sync task 877
      named 39 of NetBox's 50 addresses (24 management, 13 production, 2 IoT), and DNSsync made
      all 39 records, read back
- [x] Pi-hole 1's records imported (2026-10-19, through NetBox's API, read back): of its 130
      name and address pairs, 40 were NetBox's already (ExternalDNS's), 20 are the seed's to
      make, and the other 70 went in, each into its zone and described "imported from Pi-hole 1,
      2026-10-19"; no name clashed with another address
- [x] The sync, `docker/netbox/dns-sync/netbox_pihole_sync.py` (tests in `scripts/tests/`), as
      the stack's `netbox-dns-sync` service (#88; #91 let it reach NetBox by its service name).
      Its report-only round (Run Command task 881): on each Pi-hole the 5 two-name lines out,
      their 10 names back one per line, and the 19 records the seed made added, nothing else.
      With `SYNC_APPLY=true` (task 882, 2026-10-19) both went from 125 lines to 149, each read
      back as matching NetBox, and the next round had nothing to do. Every NetBox name looked up
      on both Pi-holes before and after: none lost an address; all 145 now answer as NetBox
      says, except Pi-hole 2's own name asked over its management address, which Pi-hole
      answers with that interface's address (its own hostname, not a record)
- [ ] The switch: ExternalDNS's Pi-hole releases out, nebula-sync's hosts and CNAME copy off,
      `proxmox-acme.yml`'s record from NetBox; the Pi-holes checked to answer the same names
      before and after
- [ ] Deletes on; a record removed in NetBox gone from both Pi-holes within a minute

## Resuming

The first unticked box is next. A half-done pass is safe: until the switch, ExternalDNS and the
other writers still write the Pi-holes, and the sync only adds.

## Done when

Every local record is in NetBox with one owner, both Pi-holes answer exactly NetBox's records in
its zones, and nothing else writes them; then this file moves to `old-queues/` and
`QUEUE-netbox.md`'s phase 4 is ticked.

## Checked and negative (2026-10-19)

- `octodns-pihole` 0.1.0 (its only release): it looks for the zone's name, which octoDNS
  always ends with a dot, inside Pi-hole's names, which never have one, so it finds no record
  and would add every one again; it writes names with that trailing dot; and it reads a line
  holding two names as one name.
- Pi-hole 1 holds 125 lines (130 name and address pairs, 5 lines with two names, no CNAMEs);
  NetBox DNS held only the 40 ExternalDNS records, all of them on Pi-hole 1 too.
