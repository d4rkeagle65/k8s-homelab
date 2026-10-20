# NetBox: the homelab documented, then the source of truth

**The ask (2026-10-15):** move NetBox to the Docker VM, then **make it the source of
Semaphore's hosts and host variables (the primary goal)**, in place of the static
inventories pasted into Semaphore. After that: document the rest of the homelab in it, make it
the owner of local DNS (ExternalDNS's records included) synced to the Pi-holes, and have it
supply new guests' addresses so `TF_VAR_site` needs fewer hand edits; with the plugins the
owner picked. NetBox is v4.7.2, a Dockhand stack on the Docker VM (`docker/netbox/`); it was
empty when this started: no sites, devices, VMs, prefixes or addresses.

## Settled

- **Nothing site-specific comes into git for this.** Hostnames and addresses live in NetBox
  (and `TF_VAR_site` until NetBox replaces it), never in this repo.
- **Once Semaphore's inventories come from NetBox, a NetBox outage stops the playbooks** (the
  reboot and upgrade ones included) unless the inventory plugin's cache answers. Both live on
  the Docker VM, so they're mostly up or down together; the cache and a kept copy of the
  static inventories cover the rest until the switch has proved itself.
- **Before anything reads from NetBox** (DNS, OpenTofu), NetBox moves out of the cluster onto
  the Docker VM, beside Vaultwarden and Semaphore, for the same reason they're there: OpenTofu
  builds the cluster's VMs, so it can't depend on a service running on them. Room checked
  2026-10-15: the Docker VM's lowest free memory over a week was 9.1 of 11.7 GiB; NetBox uses
  about 1 GiB (web 609 MiB, worker 256 MiB, database about 160 MiB, cache 6 MiB).
- **NetBox moves first, and its plugins are built there** (the owner, 2026-10-15): a Dockhand
  stack, `docker/netbox/`, whose compose file builds the image on the Docker host (`build: .`,
  as `kea/` and `claude-code/` do) from the upstream image plus a pinned
  `plugin_requirements.txt` (`uv pip install`, then `collectstatic` with a throwaway
  SECRET_KEY, as netbox-docker documents). No registry, no CI. The stack's `plugins.py` is
  the one owner of PLUGINS and PLUGINS_CONFIG: the build's `collectstatic` reads it, and so
  does NetBox at runtime. Renovate bumps the base and each plugin. **A NetBox minor upgrade
  waits for every plugin to support it** (floorplan, reorder-rack and topology-views each pin
  one minor per release); Renovate groups NetBox with its plugins.
- **On the Docker VM, NetBox logs in by OIDC with Authentik** (the owner, 2026-10-15), as
  Authentik documents for NetBox (`social_core`'s OpenIdConnectAuth, a pipeline mapping the
  `groups` claim to NetBox groups, `authentik Admins` superusers). Its port is reachable from
  the whole management network there, so the header login the cluster uses (remote user,
  safe only behind a NetworkPolicy that admits Traefik alone) would let anyone send
  `X-authentik-username`. Traefik still serves it, now without forward auth; the API takes
  tokens as before.
- **A fresh NetBox, not a moved database** (2026-10-15): it held two users, its groups, one
  API token (Homepage's) and a dashboard. Users and groups come back at the first OIDC login;
  Homepage's token is made again into `NETBOX_HOMEPAGE_TOKEN`.
- **The image is proved before the stack** (2026-10-15): NetBox v4.7.2 (Python 3.14) with the
  fourteen plugins at the versions in `plugin_requirements.txt` installs with `uv`, passes
  `manage.py check` and migrates an empty PostgreSQL 18, in a throwaway pod. Config Diff
  refuses to start without `USERNAME` and `PASSWORD` in its PLUGINS_CONFIG (the devices'
  login), so they're stack variables.
- **Proxmox to NetBox by a playbook, not ProxBox:** ProxBox (`netbox-proxbox`) supports 4.7 but
  is pre-1.0, changes weekly and needs its own `proxbox-api` backend service. A Semaphore
  playbook with the `netbox.netbox` collection, reading Proxmox's API and the cluster, does
  the same with nothing new to run.
- **The plugins (the owner, 2026-10-15): every row marked "yes" below,** fourteen: NetBox
  DNS, Attachments, Prometheus SD, Floorplan, Data Flows, Kea, the DHCP model, Config Diff,
  Topology Views, Security, Custom Objects, Routing, Inventory, Reorder Rack. Not Documents
  (Attachments instead), Interface Synchronization, IPCalculator or Nbservice; any of them can
  join once it supports the NetBox version running.
- **Ingress records reach NetBox through a custom ExternalDNS webhook provider** (the owner,
  2026-10-15), so a new hostname is in NetBox within ExternalDNS's interval, as it reaches the
  Pi-holes today. ExternalDNS's webhook API is small (negotiate the domain filter, list
  records, adjust endpoints, apply changes), served beside ExternalDNS in its pod; the
  provider keeps its records apart from the hand-made ones by a tag or an ownership field.
- **Kea's configuration stays in git** (`docker/kea/`): the only tool that pushes NetBox data
  to Kea (`netbox-kea-dhcp`) is an alpha from 2023. `netbox-kea` shows Kea's leases and
  subnets in NetBox instead.

## Phases

1. **NetBox onto the Docker VM, with its plugins:** the Dockhand stack (NetBox, its worker,
   PostgreSQL, Valkey), its real values as Dockhand variables; the database moved by dump and
   restore and the media copied (NetBox is still empty, so both are small); served through
   Traefik as an external service with Authentik's forward auth and the token-only API route
   as now; backed up by Databasus with the Docker VM's other databases; the in-cluster release
   removed once the new one is checked. The Kea plugin reaches Kea's control agent over the
   Docker network (the stack joined to Kea's): inside a container `127.0.0.1` is the
   container itself, not the host.
2. **Semaphore's inventories from NetBox (the primary goal):**
   - **The data, imported once:** a one-off script reads Semaphore's static inventories
     through its API and creates in NetBox the Proxmox hosts (devices), the Pi-holes, the
     Docker VM and the APs (VMs, containers or devices), each with its management address as
     primary IP (prefixes first), so no address passes through git or chat. Groups become
     tags (`proxmox`, `pihole`, `docker_vm`, `openwrt_aps`), variables become config contexts
     on them (`dns_guest_tag` on `proxmox`, `allow_dns_outage` on `pihole`, `ansible_user`
     and `ansible_python_interpreter` on all Debian hosts). Per-host variables (an `evacuate`
     list, say) are local config contexts on that host.
   - **The inventory:** `ansible/inventory/netbox.yml` with the `netbox.netbox.nb_inventory`
     plugin: hosts by tag into those groups, `ansible_host` from the primary IP, config
     contexts as variables. NetBox's URL and a read-only token are the semaphore stack's
     variables in Dockhand (a template takes one variable group, and they differ), never git. The collection (`ansible/collections/requirements.yml`,
     which Semaphore installs) and whatever Python it needs in Semaphore's container: check
     first, as `docker/semaphore/` runs the upstream image.
   - **The switch, template by template:** each Semaphore template moves to a File inventory
     on that path; its first run in check mode, its host list and variables compared with
     the static inventory's (`ansible-inventory --list` both ways); the static inventories
     kept until every template has run on NetBox's. The inventory cache lasts five minutes
     (`netbox.yml`): a run while NetBox is down fails rather than act on an old listing.
3. **Phase A, documentation:** `ansible/netbox-sync.yml`, scheduled in Semaphore, idempotent:
   every VM and container with its host, CPU, memory, disks and interfaces, their addresses,
   and the Kubernetes nodes' roles, read from Proxmox and the cluster. It only adds and
   updates what phase 2 entered by hand; HA moves and failovers show up by the next run.
4. **DNS owned by NetBox** (NetBox DNS), synced to the Pi-holes: done 2026-10-19, as
   `old-queues/QUEUE-netbox-dns.md` records (its settled decisions and checked negatives, octoDNS
   among them).
5. **Phase B, OpenTofu from NetBox:** new guests' addresses from `netbox_available_ip_address`
   in a prefix, and their names and intended hosts from their NetBox records, so `TF_VAR_site`
   keeps only the stable site facts. Underneath (independent of NetBox, can come first):
   `ignore_changes = [node_name]` on the HA and Talos VMs so HA moves and migrations never need
   an edit, and new guests placed on a host OpenTofu picks from free memory.

## Plugins (checked 2026-10-15 against NetBox 4.7)

| Plugin (pip name) | What it's for | 4.7 | Notes |
|---|---|---|---|
| NetBox DNS (`netbox-plugin-dns`) | Zones, records, views; A/PTR from IPAM | yes (1.7) | Needs `dnspython`. Phase 4's source. |
| NetBox Attachments (`netbox-attachments`) | Files on any object type you configure | yes (12.x) | Python 3.12-3.14. |
| NetBox Documents (`netbox-documents`) | Typed documents (manuals, contracts, diagrams) or links, on sites, devices, circuits, VMs | unclear (says 4.6+) | Wait for a 4.7 release, or use Attachments. |
| Prometheus SD (`netbox-plugin-prometheus-sd`) | Prometheus `http_sd` targets from devices, VMs, services, IPs | yes (2.x) | Prometheus needs a NetBox token. |
| Floorplan (`netbox-floorplan-plugin`) | Site and location floorplans with racks | yes (0.10.0) | One NetBox minor per release. |
| Interface Synchronization (`netbox-interface-synchronization`) | A device's components against its device type | unclear | Tested only on 4.5.8; needs `numpy`. Try in check first. |
| IPCalculator (`netbox-ipcalculator`) | Subnet calculator on prefix and IP pages | unclear | Idle since 2025-07. |
| Data Flows (`netbox-data-flows`) | Application data flows (source, destination, ports) | yes (1.5.4) | Only the latest version is supported. |
| Kea (`netbox-kea`) | Kea's status, leases and subnets in NetBox; delete leases | yes (2.0.1) | Needs Kea's API with the `lease_cmds` and `subnet_cmds` hooks, reachable from NetBox. |
| DHCP model (`netbox-plugin-dhcp`) | DHCP servers, subnets, pools, reservations as data | yes (0.2.0) | Early (0.x); pushes nothing. Optional. |
| Config Diff (`netbox-config-diff`) | A device's rendered config against its live one over SSH; push configs | yes (2.16.0) | Device credentials in `PLUGINS_CONFIG` (a Secret); exact pins on scrapli, hier-config, netutils. For the switches and APs. |
| Topology Views (`netbox-topology-views`) | Topology maps from cables; draw.io and PNG export | yes (4.7.0) | The image must create `static/netbox_topology_views/img`. |
| Security (`netbox-security`) | Firewall zones, policies, NAT, address lists | yes (1.6.x) | Python 3.12+. |
| Custom Objects (`netboxlabs-netbox-custom-objects`) | New object types without code | yes (0.7.0) | Open source; 0.x and very active. |
| Nbservice (`nb-service`) | Service and application mapping | **no** (max 4.2.99) | Stale since 2025-01. Custom Objects or Data Flows cover part of it. |
| Routing (`netbox-routing`) | Static routes, BGP, OSPF, EIGRP, IS-IS | yes (0.5.0) | Needs `django-polymorphic`; conflicts with `netbox-bgp`. |
| Inventory (`netbox-inventory`) | Assets, purchases, deliveries | yes (2.7.x) | |
| Reorder Rack (`netbox-reorder-rack`) | Drag and drop devices within a rack | yes (1.1.5) | One NetBox minor per release. |

## Checklist

- [x] Owner's choices (2026-10-15): every "yes" plugin, Attachments not Documents, the custom
      ExternalDNS webhook for the Ingress records, NetBox moved first and the inventories next
- [ ] Phase 1. Done 2026-10-15/16: `docker/netbox/` (the image proved in a throwaway pod from
      the stack's own files: all fourteen plugins load, 540 migrations, the OIDC settings and the
      group mapping); the stack running on the Docker host (Dockhand stack 10, its webhook
      Provider); the cutover commit (Authentik's NetBox blueprint to OIDC with
      `AUTHENTIK_NETBOX_OIDC_CLIENT_SECRET`, base64 in Vaultwarden and decoded in Dockhand like
      the other clients'; NetBox off the forward-auth outpost; `external-services/netbox.yaml` to
      port 8085; the in-cluster release and its chart's HelmRepository out of git; Homepage's
      tile). Left:
  - [x] Login through Authentik works (2026-10-16); every plugin's API answers through a
        read-only token (all fourteen listed at `/api/plugins/`). Each plugin's UI pages: a look
        in the browser when they're first used
  - [x] Homepage's token made again (2026-10-16); its tile reads NetBox's status
  - [x] Backups (2026-10-16): Databasus joins NetBox's network (`NETBOX_NETWORK`) and backs
        `netbox` up daily; the first backup taken
- [ ] Phase 2. Done 2026-10-16: NetBox seeded by `scripts/netbox_seed.py` (names in
      `netbox_seed.yaml`; the write token `~/.netbox-token` in the claude-code container, saved
      with `save-secret .netbox-token`, a path relative to home): the site, VLANs, prefixes,
      DHCP ranges, the Proxmox nodes' interfaces, every guest's NICs with MAC, VLAN and
      address; a second run changes nothing. Ansible's groups (tags) and their variables
      (config contexts) were imported once from Semaphore's static inventories: NetBox owns
      them now, and the seed leaves tags, contexts and the access points' devices alone. Every Ansible template in Semaphore runs on the File
      inventory "NetBox" (`ansible/inventory/netbox.yml`, key `semaphore-shared`), each
      checked against its last static run; the static inventories and the per-group keys
      `semaphore-to-proxmox`, `-docker`, `-pihole` and `-k8s` deleted by the owner. Left:
  - [ ] VLAN 4 (`vmbr4` on `eth5.4`, on every Proxmox node) comes out too (the owner,
        2026-10-19), as VLAN 9 did: in `proxmox-host.yml`'s `retired_interfaces`, whose run
        refuses while a guest or kept bridge names one. NetBox has no VLAN or prefix for it,
        only the ten interfaces. Done 2026-10-19: the owner's run (task 905) removed
        both on all 5, each read back gone with its default route kept. Left: the ten
        interfaces deleted in NetBox (the sync, task 902, brought them back before the run).
        Task 909 listed exactly those ten as stale;
        `prune_node_interfaces` is on from 2026-10-20, so the next sync deletes them. Left:
        that run's log read for ten deletes, and none left in NetBox
- [x] Phase 3 (2026-10-16): `scripts/netbox_seed.py` runs nightly at 23:00 from Semaphore's
      template "NetBox: Sync from Proxmox and Kea" (Python, `--apply`; variable group "Netbox
      Sync": `PROXMOX_URL` = `https://proxmox.<domain>`, `KEA_URL` =
      `http://kea-dhcp4:8000/`, secrets of type environment variable `NETBOX_WRITE_TOKEN`,
      `PROXMOX_TOKEN` = `PVEAPIToken=netbox-sync@pve!semaphore=…` (PVEAuditor), `KEA_API`). It
      adds and updates, never deletes: a guest gone from Proxmox stays in NetBox until removed
      by hand
- [x] Phase 4 (2026-10-19): NetBox owns every local record. ExternalDNS writes its Ingress
      records into NetBox only (`external-dns-netbox`, policy sync); the seed names each device's
      and VM's addresses in their prefix's zone; the rest were imported from Pi-hole and are kept
      by hand; `netbox-dns-sync` keeps both Pi-holes exactly matching, deletes included
      ([old-queues/QUEUE-netbox-dns.md](old-queues/QUEUE-netbox-dns.md))
- [ ] Phase 5: OpenTofu reads each guest from NetBox, so `TF_VAR_site` stops carrying a
      second, hand-kept copy of it. One owner per value: NetBox for each guest's VMID, node,
      name, addresses and MACs; `TF_VAR_site` keeps the stable facts (each network's prefix
      and gateway, the nodes' DNS servers and search domain, the Talos cluster values).
      Written 2026-10-16 (`tofu/proxmox/netbox.tf`: one GraphQL query through NetBox's route
      by the `http` provider, a read-only token; `local.guest` replaces `var.site.guests`;
      the Docker VM and the Talos VMs ignore `node_name`, so an HA move is never a change;
      the retired LXC nodes out). Checked before pushing: for all ten guests, NetBox's node,
      name, last octet and the Docker VM's MACs equal the "OpenTofu site (Proxmox)" note's.
      Left:
  - [x] Live 2026-10-16: `TF_VAR_netbox_url` and `TF_VAR_netbox_token` in Semaphore's
        "OpenTofu: Proxmox and Talos" group; its plan (task 724) read NetBox and found no
        changes. 101-106 stay in the state until an apply with a change writes the refresh
  - [x] `guests` out of `TF_VAR_site`, in Semaphore's copy and the Vaultwarden note "OpenTofu
        site (Proxmox)" (the owner, 2026-10-16)
  - [ ] The NetBox-first workflow (tofu/README.md) worked with a test guest, 2026-10-16:
        Planned in NetBox, built by an apply (task 725), marked active by the sync, which
        reused the hand-entered interface and address, then destroyed by an apply. Left:
        the test container (VMID 199) kept in NetBox as Decommissioning, the guests-to-git
        generator's skip case (the owner)
- [ ] Phase 6: each guest's shape (sizes, roles, disks, bridges) edited in NetBox and brought
      into git by a pull request from Semaphore: [QUEUE-netbox-shape.md](QUEUE-netbox-shape.md)
- [ ] The switches' cables: the core switch (TL-SG3428, 24 copper and 4 SFP ports), 02 and 04
      (TL-SG105E) are in NetBox with their ports and management addresses (role
      Switch, untagged `ansible`, so no inventory takes them), but no cable. Which port goes to
      which host and AP is the owner's to say; until then Topology Views has nothing to draw

## Resuming

The first unticked box is next. NetBox's own pages show what phase A has synced; until the
move NetBox is the `netbox` HelmRelease, after it the Dockhand stack.

## Done when

Every host, guest, address and network is in NetBox and stays current without hand edits;
local DNS is NetBox's and the Pi-holes copy it; a new guest needs no `TF_VAR_site` edit; the
chosen plugins run on 4.7; and this file moves to `old-queues/`.

## Checked and negative (2026-10-15)

- No ExternalDNS provider for NetBox, built in or published as a webhook.
- NetBox DNS pushes to no DNS server itself; it's a source for octoDNS or BIND.
- `jvoss/octodns-pihole` is archived; the PyPI package is now roosnic1's fork.
- `nb-service` doesn't load on 4.7.
- No tool both pushes NetBox data to Kea and reads Kea back; the push one is an alpha.
