# Kea DHCP on the Docker host

Replaces the dnsmasq DHCP VM. Kea serves every VLAN from the Docker host,
with the firewall relaying each VLAN's DHCP to it. In phases:

1. **Kea with file config** (this folder): subnets, options and
   reservations in a site file on the host; managed through Kea's API.
2. **Stork and a reservations database**: ISC's web UI, with reservations
   moved into Postgres so they can be added and changed live (Kea's
   `host_cmds` hook), from Stork or the API. Stork moves the file's
   reservations into the database itself ("Migrate").
3. **High availability and DNS**: a second Kea as a hot-standby partner on
   another host (the relay can point at both), and lease names fed into
   DNS so Pi-hole resolves every device.

## What lives where

| | |
|---|---|
| `compose.yaml`, `kea-dhcp4.conf`, `Dockerfile`, `initdb/` | here, in git: nothing site-specific |
| `/opt/kea/site/site.json` | on the host: subnets, pools, options, the reservations database's connection |
| `/opt/kea/site/api-credentials` | on the host: one `user:password` line for Kea's API (Stork's agent reads it too) |
| `/opt/kea/lib/` | on the host: the lease file |
| `/opt/kea/postgres/` | on the host: PostgreSQL (reservations, and Stork's own data) |
| `/opt/kea/stork-agent/` | on the host: the Stork agent's certificates |

The site file and credentials never go in git: they're full of addresses,
MACs and hostnames. `kea-dhcp4.conf` includes the site file at the end.

## Phase 1

### 1. Host preparation (as root)

```sh
install -d -m 700 /opt/kea/site
install -d /opt/kea/lib
printf 'admin:%s\n' "$(openssl rand -hex 24)" > /opt/kea/site/api-credentials
chmod 600 /opt/kea/site/api-credentials
```

Keep a copy of the credentials line on the admin PC, outside the repo, for
the API and the import script.

### 2. Generate the site file

From dnsmasq's config files and leases file, copied somewhere outside the
repo:

```sh
python docker/kea/tools/dnsmasq_to_kea.py --conf dnsmasq.conf [--conf other.conf] \
    --leases dnsmasq.leases --out-dir <outside the repo>
```

- Every `CHECK:` line in its report needs a look before going live: options
  without a Kea equivalent, hostname-only reservations, `ignore` hosts,
  subnets where dnsmasq sent its own address as the router or DNS server.
- dnsmasq tags each request with the name of the interface it came in on.
  Options matched by such a tag need `--tag-subnet <interface>=<subnet>`.

Check the result with Kea itself before copying it over:

```sh
docker run --rm -v ./kea-dhcp4.conf:/etc/kea/kea-dhcp4.conf:ro -v /opt/kea/site:/etc/kea/site:ro \
    docker.cloudsmith.io/isc/docker/kea-dhcp4:3.2.1 kea-dhcp4 -t /etc/kea/kea-dhcp4.conf
```

Then copy `site.json` to `/opt/kea/site/`.

**Check every pool for devices with fixed addresses.** dnsmasq pings an
address before handing it out; Kea doesn't, so a fixed address inside a
pool would eventually be handed to a second device. Ping each pool address
and compare with the leases and reservations; move the pool (or reserve the
address) where something answers that has neither.

### 3. Deploy

Add this folder as a Git stack in Dockhand, with `KEA_DHCP_ADDRESS` (the
host address the firewall relays to) and `KEA_API_ADDRESS` (the management
address). Then check the API answers:

```sh
curl -s -u "$(cat <credentials file>)" -H 'Content-Type: application/json' \
    -d '{"command": "status-get"}' http://<api address>:8000/
```

On the host itself, and in its host-network containers, use
`http://127.0.0.1:8000/` instead. The management address hangs from there:
connections to it leave from it, and the management routing rules send
them out to the network instead of to Kea's container.

### 4. Import the leases

Before the relay points at Kea, so every client keeps its address; repeat
it with a fresh copy right before each VLAN moves, for the VLANs still on
the old server (never over leases Kea issued itself):

```sh
python docker/kea/tools/import_leases.py --url http://<api address>:8000/ \
    --credentials-file <credentials file> leases-import.json
```

### 5. The firewall's DHCP relay

On the CloudGen firewall: **CONFIGURATION > Configuration Tree > Box >
Assigned Services > DHCP-Relay** (create the DHCP Relay service first if
it isn't there).

1. Lock, then tick **Enable Relay for IPv4**, UDP port 67.
2. **DHCP Server IPs**: the Docker host's address (`KEA_DHCP_ADDRESS`).
3. **Relay Interfaces**: the interface toward the Docker host (the server
   side, always), plus each VLAN as it moves (step 6). Each must have an address in its network;
   that address is the relay address Kea selects the subnet by, so it must
   lie inside the subnet's range in `site.json`.
4. Leave Option 82 off. Send changes and activate.

The relay and the firewall's own DHCP service can't share an interface.

### 6. Move one VLAN at a time

Two servers answering on one VLAN would hand out conflicting leases, so
for each VLAN, in this order:

1. Stop dnsmasq answering there (`no-dhcp-interface=<interface>`, restart
   dnsmasq).
2. Add the VLAN's interface to the relay and activate.
3. Release and renew a client there (`ipconfig /release` then `/renew`, or
   `dhclient -r` then `dhclient`), so it broadcasts instead of asking the
   old server directly, and check Kea's statistics and `lease4-get-all`.

Clients that only renew later find the old server gone, broadcast, and get
their imported lease back from Kea through the relay. An early renewal of
a freshly imported lease shows the imported times: Kea reuses a lease
that's early in its life instead of rewriting it. When every VLAN has
moved, give it one lease time, then shut the dnsmasq VM down.

## Phase 2: Stork and the reservations database

Stork (ISC's web UI for Kea) and a PostgreSQL database for reservations, in
the same stack. ISC publishes no Stork container images, so `Dockerfile`
builds one (`homelab/kea-tools`) on ISC's Kea image from ISC's own
packages: the Stork agent and server, and `kea-admin`. Kea's version in it
must match the `kea-dhcp4` service's.

- **Stork's agent** shares the `kea-dhcp4` container's network and process
  namespaces: it finds Kea in the process list, reads the same config files
  at the same paths, runs Kea's binary to learn its version, and uses the
  API on localhost with the credentials from the site directory.
- **Reservations live in the database**, where `host_cmds` (and so Stork)
  adds, changes and removes them live. A reservation still in the site file
  overrides the database's for the same client, so they're moved, not
  copied.
- **Subnets and pools stay in the site file.** Stork's subnet editing and
  its "Migrate reservations" both end with Kea writing its whole config
  back to its file (`config-write`), which can't work here: the main file
  is read-only from git and includes the site file. Change subnets in the
  site file and reload; move reservations with the API.

### 1. Host preparation (as root)

```sh
install -d /opt/kea/postgres /opt/kea/stork-agent
openssl rand -hex 24
```

Generate three passwords that way, and set them in the stack's environment
in Dockhand as secrets: `POSTGRES_PASSWORD`, `KEA_DB_PASSWORD`,
`STORK_DB_PASSWORD`. Then add the database's connection to the site file,
with the same `KEA_DB_PASSWORD`, as its first member:

```json
"hosts-databases": [ { "type": "postgresql", "name": "kea", "user": "kea",
  "password": "<KEA_DB_PASSWORD>", "host": "kea-postgres", "port": 5432,
  "retry-on-startup": true, "on-fail": "serve-retry-continue",
  "max-reconnect-tries": 100, "reconnect-wait-time": 3000 } ],
```

`serve-retry-continue` keeps DHCP serving if the database is down.

### 2. Redeploy

Redeploy the stack in Dockhand. It builds `homelab/kea-tools` on the host
the first time (a minute or two); `kea-postgres` creates the two databases
on its first start (`initdb/`), and `kea-db-init` creates Kea's tables (or
upgrades them after a Kea update), then exits. Kea restarts once, a few
seconds without DHCP, which clients ride out.

If Dockhand doesn't build images from a Git stack, build it once on the
host from Dockhand's clone: `docker compose build` in this folder.

### 3. Stork

Open `http://<api address>:8080`, log in as `admin`/`admin` and set a new
password. The agent registers itself on start: approve it under **Services
> Machines** (check its token fingerprint), and Kea appears.

### 4. Move the reservations into the database

For each reservation in the site file: `reservation-add` with
`"operation-target": "database"`, then remove the `reservations` lists from
the site file and `config-reload`. Check with `reservation-get-all`, and in
Stork under **DHCP > Host Reservations**.

## Changing the site file later

Edit `/opt/kea/site/site.json`, check it (step 2), then reload without a
restart:

```sh
curl -s -u "$(cat <credentials file>)" -H 'Content-Type: application/json' \
    -d '{"command": "config-reload"}' http://<api address>:8000/
```

Kea's API also serves `lease4-get-all`, `lease4-get-by-hostname`,
`statistic-get-all` and `config-get`.
