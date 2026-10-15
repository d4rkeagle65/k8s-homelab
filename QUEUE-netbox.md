# NetBox: the homelab documented, then the source of truth

**The ask (2026-10-15):** use NetBox to document the homelab (hosts, guests, addresses,
networks), have it supply new guests' addresses so `TF_VAR_site` needs fewer hand edits, make
it the owner of local DNS (ExternalDNS's records included) synced to the Pi-holes, and add the
plugins the owner picked. NetBox is v4.7.2 (the community chart, `prod/services/netbox/`) and
was empty when this started: no sites, devices, VMs, prefixes or addresses.

## Settled

- **Nothing site-specific comes into git for this.** Hostnames and addresses live in NetBox
  (and `TF_VAR_site` until NetBox replaces it), never in this repo.
- **Documentation first, no dependency:** phase A only writes to NetBox, so NetBox being down
  breaks nothing.
- **Before anything reads from NetBox** (DNS, OpenTofu), NetBox moves out of the cluster onto
  the Docker VM, beside Vaultwarden and Semaphore, for the same reason they're there: OpenTofu
  builds the cluster's VMs, so it can't depend on a service running on them. Room checked
  2026-10-15: the Docker VM's lowest free memory over a week was 9.1 of 11.7 GiB; NetBox uses
  about 1 GiB (web 609 MiB, worker 256 MiB, database about 160 MiB, cache 6 MiB).
- **Plugins mean a custom image:** `docker/netbox/` (FROM the upstream image at the chart's
  NetBox version, plugins from a pinned requirements file, `collectstatic`), built by a GitHub
  Actions workflow (the repo's first) into a public `ghcr.io` image, since it holds no secret.
  Renovate bumps the base and each plugin. **A NetBox minor upgrade waits for every plugin to
  support it** (several pin one minor per release: floorplan, reorder-rack, topology-views);
  Renovate groups NetBox with its plugins.
- **Proxmox to NetBox by a playbook, not ProxBox:** ProxBox (`netbox-proxbox`) supports 4.7 but
  is pre-1.0, changes weekly and needs its own `proxbox-api` backend service. A Semaphore
  playbook with the `netbox.netbox` collection, reading Proxmox's API and the cluster, does
  the same with nothing new to run.
- **Kea's configuration stays in git** (`docker/kea/`): the only tool that pushes NetBox data
  to Kea (`netbox-kea-dhcp`) is an alpha from 2023. `netbox-kea` shows Kea's leases and
  subnets in NetBox instead.

## Phases

1. **The image and the plugins** (below), deployed where NetBox runs now.
2. **Phase A, documentation:** `ansible/netbox-sync.yml`, scheduled in Semaphore, idempotent:
   the Proxmox cluster, each host as a device, every VM and container with its host, CPU,
   memory, disks and interfaces, their addresses (cloud-init and guest configs), and the
   Kubernetes nodes' roles. HA moves and failovers show up by the next run. The owner creates
   the prefixes and VLANs by hand once: the one thing the sync can't discover.
3. **NetBox onto the Docker VM:** a Dockhand stack (NetBox, its worker, PostgreSQL, Valkey) from
   the custom image, served through Traefik as an external service with Authentik's
   forward auth as now; the database moved by dump and restore; backups as the Docker VM's
   other databases.
4. **DNS owned by NetBox** (NetBox DNS), synced to the Pi-holes:
   - Ingress hostnames into NetBox: either **(B, preferred)** an in-cluster CronJob reading the
     Ingresses and upserting them as records tagged as Kubernetes's, or **(A)** a custom
     ExternalDNS webhook provider for NetBox DNS, near-instant but a service to maintain. No
     ExternalDNS provider for NetBox exists (checked 2026-10-15).
   - NetBox to Pi-hole 1 by octoDNS: `octodns-netbox-dns` as the source, `octodns-pihole`
     (the roosnic1 fork on PyPI, Pi-hole v6 through `pihole6api`; local A, AAAA and CNAME
     records only, no TTLs) as the target; nebula-sync copies Pi-hole 2 as now.
   - **One owner:** NetBox holds every local record (hand-made ones, the hosts' that
     `proxmox-host.yml` writes today, the Ingresses'), and the Pi-holes become copies. Until
     the switch, the writers stay as they are: ExternalDNS (`upsert-only`), `proxmox-host.yml`
     (whose `pihole-FTL` write replaces the whole list), and the owner by hand.
   - NetBox DNS's IPAM sync can then make A and PTR records for documented addresses.
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

- [ ] Owner: which plugins go in the first image (the "yes" rows, or fewer), and Documents or
      Attachments
- [ ] `docker/netbox/` image, its GitHub Actions workflow and Renovate rules; the HelmRelease on
      it with `plugins` and `pluginsConfig`; each plugin's page checked in NetBox
- [ ] Owner: a NetBox API token for Semaphore (write), the prefixes and VLANs entered by hand
- [ ] `ansible/netbox-sync.yml` and its Semaphore template and schedule; a dry run, then a run,
      then a second run changing nothing
- [ ] Owner decides the move to the Docker VM; then the Dockhand stack, dump and restore, the
      Traefik external service, the old release removed
- [ ] Phase 4: B or A for the Ingresses; octoDNS stack; the record owners switched over, the
      Pi-holes checked to answer the same names before and after
- [ ] OpenTofu: `ignore_changes = [node_name]` on HA and Talos VMs (no change in the plan), host
      picked from free memory for new guests
- [ ] Phase B: addresses and names from NetBox; `TF_VAR_site` cut down to the stable facts

## Resuming

The first unticked box is next. NetBox's own pages show what phase A has synced; `kubectl -n
netbox get hr` (or the Dockhand stack, after the move) shows where it runs.

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
