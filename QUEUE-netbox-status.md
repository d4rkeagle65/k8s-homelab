# Every NetBox job by its status

The ask (2026-10-26, the owner): every job that reads NetBox does something different
depending on a device's or VM's status, by one standard. The standard is the table in
`README.md` ("What a NetBox status means"), the rule in `CLAUDE.md`; the owner approved it.

## Settled

- The table is the standard: build, configure, sync, access, monitor and use each act on
  the statuses it names, and nothing else.
- A sync moves a status only between Planned, Active and Offline; Staged, Failed,
  Decommissioning and Inventory are the owner's and never written.
- Monitoring of Staged (scraped, no alerts) waits for a later batch: alert rules would need
  to tell it apart. Failed is scraped and alerts now.
- The Ansible inventory changes last, after the owner sets the two lab hosts (Offline in
  NetBox) to Active or Staged; otherwise the lab upgrade loses its hosts.

## Checklist (from the survey on 2026-10-26, one pull request per line or two)

1. [x] IoT devices: the NetBox sync leaves a Decommissioning one alone, and never sets it
   Offline; `ha_iot_setup.py` reads Active only (with this file's pull request).
2. [ ] `scripts/netbox_seed.py`, VMs: today writes Active or Offline from Proxmox's running
   state over every status but Decommissioning. To: only when the status is Planned, Active
   or Offline.
3. [ ] `scripts/netbox_seed.py`, Proxmox host devices: today always Active. To: as VMs.
4. [ ] `scripts/netbox_seed.py`, DNS names: today Offline and Planned VMs keep theirs. To:
   only Staged and Active get one (Use).
5. [ ] `termix-netbox-sync.py`: today Active only. To: Active, Staged and Failed (Access).
6. [ ] Prometheus's NetBox discovery (`scrapeconfig-docker-vm*.yaml`, `scrapeconfig-firewall.yaml`):
   today Active only. To: Active and Failed (Monitor); Staged later.
7. [ ] `tofu/proxmox` (`netbox.tf`, `guests.tf`, `docker-vms.tf`, `talos.tf`): today start at
   boot off only for Decommissioning. To: off for Offline too (Build: kept, not started). Its
   plan read by the owner before an apply.
8. [ ] `tofu/tailscale`: today ignores status. To: routes, ipsets and DNS from Staged and
   Active only (Use). Its plan read by the owner.
9. [ ] The Ansible inventory (`ansible/inventory/netbox.yml`): today every host tagged
   ansible. To: Staged and Active only (Configure). First the owner sets cotedeprox01/02 to
   Active or Staged.

Unchanged and already right: `proxmox-endpoints.py`, `netbox_pihole_sync.py` (its own
records' status), `netbox-dns-webhook.py` (its own records), `netbox_guests.py`
(Decommissioning copied from main; Planned included so OpenTofu builds it).

## Resume

The first unticked line is next. Each pull request ticks its line here, in the same commit.
Done when line 9 is ticked; then this file moves to `old-queues/` with a row in its README.
