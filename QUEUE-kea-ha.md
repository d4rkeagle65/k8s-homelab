# Kea in two places: its reservations on CloudNativePG, then a hot-standby partner

**The ask (decision 5, the owner, 2026-10-16: (b)):** DHCP survives the Docker VM's host
going down. Kea's reservations move from PostgreSQL on the Docker VM (`kea-postgres` in
`docker/kea/`) to a CloudNativePG cluster, reached through a LoadBalancer address, so a second
Kea can read them while the Docker VM is down; then that second Kea runs on a second
Docker VM, on another host, as a hot-standby partner, the firewall relays to both, and lease names reach DNS.
`QUEUE.md`'s "Kea phase 3"; `docker/kea/README.md` has phases 1 and 2.

## Settled

- **Only Kea's reservations move.** Stork's own database stays in `kea-postgres` on the Docker
  VM: Stork is a UI, and losing it with that host costs nothing DHCP needs.
- **One database, `kea`, owner `kea`, on CloudNativePG** (PostgreSQL 18, two instances on
  `local-db`, archived to Garage like every cluster), in a namespace `kea` (category
  `services`). Its schema is made by `kea-admin` (Kea's version), never by hand: the
  `kea-db-init` service in `docker/kea/compose.yaml` already does that on each deploy, and is
  pointed at the new address.
- **Reached by a LoadBalancer address on the main network, TLS required.** CloudNativePG's
  `managed.services.additional` adds a `LoadBalancer` Service on the primary (`rw`) with a fixed
  MetalLB address; `pg_hba` accepts only `hostssl` for `kea`, and the Service only the two Kea
  hosts' addresses (`loadBalancerSourceRanges`). Kea 3.1+ connects over TLS (`ssl-mode`,
  `trust-anchor`: CloudNativePG's CA, from the `kea-db-ca` Secret, copied to the host).
- **The same `kea` password as today**, so the site file changes only the host and TLS.
- **The partner runs on a second Docker VM** (the owner, 2026-10-16), not a plain container: the
  same model as the first (Docker, Dockhand through a Hawser agent, `baseline.yml`), so the
  partner is a second deployment of `docker/kea/`, and the VM can take more stacks off the first
  one (a second Tailscale subnet router first). 2 vCPU, 3 GiB, 32 GB, the same five bridges;
  hand-written in `tofu/proxmox/` like the first, never on the first one's host (the owner chose
  the 62 GiB host, ~23 GiB free at peak, for game servers later).
- **`serve-retry-continue` stays:** a database that's unreachable never stops DHCP; leases and
  reservations already handed out keep working.

## Rules

- **No address, password or MAC in git.** The LoadBalancer address, the clients' ranges and
  the password are `cluster-secrets` variables; the site file is on each Kea host.
- **The cutover never leaves DHCP without its reservations:** the copy is made with Kea's
  `host_cmds` writes paused (Stork closed, no API changes), and checked row for row before the
  site file points at the new database. The old database stays until the new one has run a week.
- **Each step proves itself:** archiving (`pg_stat_archiver`), a TLS login from the Docker VM,
  `reservation-get-all` per subnet equal before and after.

## Where things live

- **In git:** `kubernetes/prod/services/kea/kea/` (hand-written: the Cluster, ObjectStore,
  ScheduledBackup, the password Secret); `docker/kea/` (the site file's shape in the README).
- **Variables** (Vaultwarden, then `cluster-secrets`; set 2026-10-16): `KEA_DB_PASSWORD` (the
  same value as Dockhand's), `KEA_DB_ADDRESS` (a free address in MetalLB's pool),
  `KEA_DB_CLIENTS` (one CIDR holding exactly both Keas' Production addresses, a `/31` for `.40`
  and `.41`, since the Service and `pg_hba` each take a single range). The role's name, `kea`,
  is in git (the owner).
- **On the hosts:** `/opt/kea/site/site.json` (`hosts-databases`), `/opt/kea/site/kea-db-ca.crt`.

## Checklist

- [x] The owner adds the three variables (above), 2026-10-16
- [x] The cluster (2026-10-16): `kea-db` healthy, its LoadBalancer `kea-db-lan` at
      `KEA_DB_ADDRESS`, WAL archived to Garage (5, none failed); from the Docker VM (Run Command
      task 754) `kea` logs in over TLS 1.3 with today's password, and `pg_hba` refuses it without TLS
- [x] The copy (2026-10-16, Run Command task 757): `kea-admin` made schema 34.0 (the old one's)
      over TLS; `hosts` copied (51 rows, the only table with data; `dhcp4_options` and
      `ipv6_reservations` empty), every row's md5 equal, the id sequence at 51 in both. A
      reservation changed in Stork before the cutover means copying again (drop and re-run)
- [ ] The cutover (the owner's run): the site file's `hosts-databases` to the new address with
      TLS, Kea restarted, `reservation-get-all` equal per subnet, a renewal seen in Kea's log
- [ ] The second Docker VM: built 2026-10-16 (apply task 770; task 771: Debian 13, cloud-init
      done, the agent up, both addresses, the default route on Production; NetBox tags `ansible`
      and `debian`, local context `ansible_user: dhardin`, become). Left: the owner's run of
      "Setting: Debian Baseline" (dry run task 772: 8 changes, all on it), then
      `ansible/docker-host.yml` (a template: survey variable `docker_host_target`, target
      "Variable"; `HAWSER_TOKEN` a secret environment variable, from Dockhand's new environment),
      then the `docker_vm` tag. In NetBox (done 2026-10-16 through the API: VMID 120, Planned, on the
      62 GiB host, 4 vCPU and 8 GiB, `net0` management and `net1` Production at `.41`, role
      "Docker VM", one 96 GB disk), then `tofu/proxmox/docker-vms.tf` builds it from Debian's
      cloud image (it needs `site.vm_login` in the "OpenTofu site (Proxmox)" note; a plan, the
      owner's apply); then NetBox's local context for its login (`ansible_user`, become) and the
      `debian` tag, `baseline.yml`, Docker, Dockhand's Hawser agent
- [ ] The partner: `docker/kea/` deployed there in a partner mode, Kea with the HA hook
      (`hot-standby`, the first Docker VM's Kea primary), both reading `kea-db`
- [ ] The firewall's relay pointed at both Keas (the owner, in its UI)
- [ ] The failover proved: the primary stopped, a client renews from the standby
- [ ] Lease names into DNS (Kea's DDNS to the Pi-holes, or a sync): its own decision when here
- [ ] A week on the new database: `kea` dropped from `kea-postgres`

## Resuming

The first unticked box is next; until the cutover, the Docker VM's database is the live one
and the new cluster is a copy nothing reads.

## Done when

Stopping the Docker VM's Kea leaves every VLAN getting addresses from the partner, with the
same reservations; then this file moves to `old-queues/`.

## Checked and negative

- Kea's TLS to PostgreSQL: supported since Kea 3.1 (`ssl-mode`, `trust-anchor`, `cert-file`);
  this stack runs 3.2.1.
