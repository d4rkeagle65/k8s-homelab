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
- [x] The cutover (2026-10-17, the owner's go-ahead, Run Command task 802): the primary's
      `hosts-databases` on `kea-db` (`ssl-mode: require`), reloaded without a restart, 51
      reservations served before and after, its connection TLS 1.3 (`pg_stat_ssl`). The previous
      site file is beside it as `site.json.before-kea-db`
- [ ] The CA: `ssl-mode` is `require`, not `verify-ca`, because CloudNativePG renews its own CA
      about every 90 days and a copy on the hosts would go stale. A long-lived CA (cert-manager,
      `spec.certificates.serverCASecret`) or a job copying the CA to both hosts, then `verify-ca`
- [ ] `kea-db-init` in `docker/kea/compose.yaml` still upgrades the old `kea-postgres` schema:
      point it at `kea-db` (with `PGSSLMODE=require`) before the next Kea version
- [ ] The HA pair (2026-10-17): `docker/kea/kea-dhcp4.conf` now carries the HA hook (its
      settings from each host's `ha.json`), the one config both Keas run; `docker/kea-standby/`
      (Kea alone; the Stork agent needs its image built on that VM, later) fetches it from main;
      `ansible/kea-ha.yml` (template "Setting: Kea HA Config") wrote both hosts' files and Kea's
      config test passed on both (task 807). Left: the standby's stack in Dockhand on the second
      Docker VM's environment (`KEA_DHCP_ADDRESS`, `KEA_API_ADDRESS`); both report
      `hot-standby`, the standby's leases synced
- [x] The second Docker VM (2026-10-16): in NetBox (VMID 120, role "Docker VM", `.41` on
      management and Production), built by OpenTofu from Debian's cloud image (task 770), the
      baseline (787) and `docker-host.yml` (794: Docker 29.9.1 held, live-restore on, Hawser
      running); NetBox tags `ansible`, `debian`, `docker_vm`, local context `ansible_user: root`
      and `management_gateway` (the management routing, `tasks/management-routing.yml`); the
      sync (795) made it active with its MACs. Semaphore logs in as root with `semaphore-shared`
      (cloud-init's `disable_root` wrapping removed, task 789; the vendor data now prevents it)
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
