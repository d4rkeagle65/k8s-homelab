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
| `compose.yaml`, `kea-dhcp4.conf` | here, in git: nothing site-specific |
| `/opt/kea/site/site.json` | on the host: subnets, pools, options, reservations |
| `/opt/kea/site/api-credentials` | on the host: one `user:password` line for Kea's API |
| `/opt/kea/lib/` | on the host: the lease file |

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

### 3. Deploy

Add this folder as a Git stack in Dockhand, with `KEA_DHCP_ADDRESS` (the
host address the firewall relays to) and `KEA_API_ADDRESS` (the management
address). Then check the API answers:

```sh
curl -s -u "$(cat <credentials file>)" -H 'Content-Type: application/json' \
    -d '{"command": "status-get"}' http://<api address>:8000/
```

### 4. Import the leases

Before the relay points at Kea, so every client keeps its address:

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
3. **Relay Interfaces**: the interface toward the Docker host, plus each
   VLAN as it moves (step 6). Each must have an address in its network;
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
3. Renew a client there (`ipconfig /renew`, or `dhclient -r` then
   `dhclient`) and check Kea's log for the lease, and `lease4-get-all`.

Clients that only renew later find the old server gone, broadcast, and get
their imported lease back from Kea through the relay. When every VLAN has
moved, shut the dnsmasq VM down.

### Changing the site file later

Edit `/opt/kea/site/site.json`, check it (step 2), then reload without a
restart:

```sh
curl -s -u "$(cat <credentials file>)" -H 'Content-Type: application/json' \
    -d '{"command": "config-reload"}' http://<api address>:8000/
```

Kea's API also serves `lease4-get-all`, `lease4-get-by-hostname`,
`statistic-get-all` and `config-get`.
