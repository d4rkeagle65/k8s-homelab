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
      about every 90 days and a copy on the hosts would go stale. kea-db's server certificate
      now comes from a ten-year CA, `kea-db-root-ca` (cert-manager,
      `kubernetes/prod/services/kea/kea/app/certificates-db.yaml`); replication keeps
      CloudNativePG's own client CA. Left, after that merge, with kea-db healthy and its
      `pg_stat_ssl` showing the new certificate: the CA's `ca.crt` (public; `kubectl -n kea get
      secret kea-db-root-ca -o jsonpath='{.data.ca\.crt}'`) into each host's site folder as
      `kea-db-ca.pem` (`ansible/kea-ha.yml`, which writes the site files); the site file's
      `hosts-databases` with `"ssl-mode": "verify-ca"` and `"trust-anchor":
      "/etc/kea/site/kea-db-ca.pem"` (Kea 3.2's PostgreSQL backend); `kea-db-init` given
      `PGSSLROOTCERT` the same, as its `PGSSLMODE` follows `ssl-mode`; then both Keas
      restarted one at a time, each serving reservations before the other
- [x] `kea-db-init` reads its database from the site file's `hosts-databases` (2026-10-17):
      `kea-db`, with its `ssl-mode`; checked in its image (task 824, schema 34.0)
- [x] The HA pair (2026-10-18): `docker/kea/kea-dhcp4.conf` carries the HA hook (its
      settings from each host's `ha.json`, written by `ansible/kea-ha.yml`, template "Setting:
      Kea HA Config"), the one config both Keas run; `docker/kea-standby/` fetches it from main
      and runs in Dockhand on the second Docker VM. Both report `hot-standby`, in touch, lease
      updates on; the standby synced the primary's 44 leases and serves the 51 reservations
      from `kea-db` (Run Command tasks 833-834). The standby's `KEA_API_ADDRESS` is its
      management address: the peers' URLs in `ha.json` are the NetBox primary IPs
- [ ] The Stork agent on the standby: `docker/kea-standby/` runs Kea alone; the agent's image
      (`homelab/kea-tools`, built on the host by the primary's stack) has to be built on the
      second Docker VM first. Until then Stork sees only the primary
- [x] The second Docker VM (2026-10-16): in NetBox (VMID 120, role "Docker VM", `.41` on
      management and Production), built by OpenTofu from Debian's cloud image (task 770), the
      baseline (787) and `docker-host.yml` (794: Docker 29.9.1 held, live-restore on, Hawser
      running); NetBox tags `ansible`, `debian`, `docker_vm`, local context `ansible_user: root`
      and `management_gateway` (the management routing, `tasks/management-routing.yml`); the
      sync (795) made it active with its MACs. Semaphore logs in as root with `semaphore-shared`
      (cloud-init's `disable_root` wrapping removed, task 789; the vendor data now prevents it)
- [x] The firewall's relay pointed at both Keas (the owner, 2026-10-18): the standby counted the
      same relayed packets as the primary, dropped as not in its scope (Run Command task 840)
- [x] The failover proved (2026-10-18): the primary's Kea stopped at 02:27:57, the standby in
      partner-down within a minute, then 18 offers and 62 acks from it overnight with no drop;
      the primary started at 13:48:44, synced 44 leases back and both were hot-standby 30
      seconds later (tasks 841-869)
- [ ] Lease names into DNS (Kea's DDNS to the Pi-holes, or a sync): its own decision when here
- [ ] A week on the new database: `kea` dropped from `kea-postgres`

## Resuming

The first unticked box is next. Since the cutover both Keas read `kea-db`; `kea-postgres` on
the first Docker VM still holds the old copy until the last box. A redeploy of either stack
restarts its Kea; with the partner up, the other serves meanwhile.

## Done when

Stopping the Docker VM's Kea leaves every VLAN getting addresses from the partner, with the
same reservations; then this file moves to `old-queues/`.

## Checked and negative

- Kea's TLS to PostgreSQL: supported since Kea 3.1 (`ssl-mode`, `trust-anchor`, `cert-file`);
  this stack runs 3.2.1.
