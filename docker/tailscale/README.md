# Tailscale on the Docker host

Replaces the dedicated Tailscale VM: the same subnet routes and exit node,
from a container with host networking (`compose.yaml`).

## Host preparation (once, as root)

1. **IP forwarding** (Docker turns on IPv4 forwarding already; this makes it
   explicit and adds IPv6):

   ```sh
   printf 'net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n' > /etc/sysctl.d/90-tailscale.conf
   sysctl --system
   ```

2. **Routing rule order.** The host routes replies from its management
   address back out of the management interface with source-based rules
   (netplan `routing-policy`, table 250). Tailscale adds its own rules at
   priorities 5210 to 5270, with the routes to tailnet peers in table 52.
   Rules at a lower number than Tailscale's would send replies to tailnet
   clients out of the management interface instead of `tailscale0`. So the
   two netplan rules need priorities above 5270 and below the main table
   (32766), for example 5300 and 5301. Apply with `netplan try`.

3. **A DMZ leg for forwarded traffic.** The subnet router doesn't source-NAT,
   and the firewall must see both directions of every connection. A host
   that sits directly on the main and management networks would deliver
   tailnet traffic there itself while the replies went through the
   firewall (triangular routing), which the firewall drops. So the host
   gets an interface in the DMZ, as the old node had, and everything the
   node forwards leaves through it:
   - a third network device on the DMZ bridge, with an address and no
     default gateway in netplan;
   - a routing table whose default route is the DMZ gateway, through that
     interface;
   - packets arriving from `tailscale0` marked (nftables, prerouting) and
     sent to that table by a netplan `routing-policy` rule on the mark,
     at a priority above Tailscale's 5270;
   - loose reverse-path checking on the DMZ interface.

   Tailscale's own encrypted traffic (UDP 41641) isn't forwarded, so it
   keeps using the host's default route; no port forward is needed.

4. **Docker's forwarding policy.** Docker sets the kernel's FORWARD policy to
   DROP and only allows its own bridges, while Tailscale's container puts
   its allow rules in the legacy iptables tables, which don't override that.
   So everything the node forwards is dropped until `DOCKER-USER` allows it,
   only between `tailscale0` and the DMZ leg (so the host never routes
   between its other networks). A oneshot unit with `PartOf=docker.service`
   re-adds the two rules whenever Docker starts:

   ```sh
   iptables -I DOCKER-USER -i tailscale0 -o <DMZ interface> -j ACCEPT
   iptables -I DOCKER-USER -i <DMZ interface> -o tailscale0 -j ACCEPT
   ```

5. **The firewall** routes 100.64.0.0/10 and the subnets other tailnet
   routers advertise (accepted with `--accept-routes`, e.g. a travel
   router's network) to the host's DMZ address. **Change these only at the
   cutover**: until the new node is running behind that address, they cut
   off every tailnet client and the LAN's access to those subnets. The
   firewall's existing rules for 100.64.0.0/10 (arriving from the DMZ) stay
   as they are. If one of its rules forces a next hop for tailnet traffic
   (a connection object with an explicit route address), point that at the
   host's DMZ address too: it overrides the routing table.
6. **Tailscale access rules.** Traffic the node forwards keeps its LAN
   addresses, so rules written for those keep working. Traffic from the
   host itself carries the node's tag; it only reaches what the policy
   allows that tag.

## Keys that don't expire

An auth key (90 days at most) is only used to register the node: with
`TS_AUTH_ONCE` and the state in `/opt/tailscale`, later starts log in with
the node's own key. That key expires too (180 days by default), unless the
node is tagged or has key expiry disabled in the admin console.

Hands-off, which also survives losing `/opt/tailscale`:

1. Create an OAuth client (Settings > OAuth clients) with the `auth_keys`
   write scope and the tag, e.g. `tag:subnet-router`. Its secret doesn't
   expire.
2. In the policy file: `tagOwners` for the tag, and `autoApprovers` for the
   advertised subnets and `exitNode`, so the node's routes need no clicks.
3. Set `TS_TAGS` to the tag and `TS_AUTHKEY` to the client secret with
   `?ephemeral=false&preauthorized=true` appended.

A tagged node is owned by the tag, not a user; that only matters to access
rules that name users as the source of its traffic.

## Changing its flags later

`TS_EXTRA_ARGS` (and so `TS_TAGS`) only applies when the node registers;
later starts reapply just `TS_ROUTES` and `TS_HOSTNAME`. To change another
flag on a running node:

```sh
docker exec tailscale tailscale set --accept-routes=false
```

Tags can't be changed that way. Either remove the node from the tailnet,
empty `/opt/tailscale` and redeploy with the new `TS_TAGS`, or re-register
in place (`docker exec tailscale tailscale up --force-reauth
--auth-key=<key> --advertise-tags=<tags>` with the same flags as
`TS_EXTRA_ARGS`).

## Cutover

The old node and this one can't run at the same time, not even briefly.
This node accepts other routers' routes (`--accept-routes`), and Tailscale
installs a peer's advertised subnets in its table 52, which the host
consults before its own routes, with no exception for subnets the host sits
on. With the old node still advertising the main, management and DMZ
subnets, this host's own traffic to them would go into the tunnel. So
deploying this stack is the cutover, and remote access over Tailscale is
down for the minute or two it takes: do it from the LAN.

1. Stop Tailscale on the old node (`systemctl stop tailscaled`) and disable
   it (`systemctl disable tailscaled`).
2. Point the firewall's 100.64.0.0/10 route and its routes to other tailnet
   routers' subnets at the host's DMZ address.
3. Deploy the stack in Dockhand (variables set, `TS_AUTHKEY` secret). With
   a tag and auto-approvers, the node's routes and exit node are approved
   as it registers; otherwise approve them in the admin console and
   disable its key expiry.
4. Test: from the LAN, the host itself (`ip route get <a main-network
   address>` must not say `tailscale0`) and a device behind another tailnet
   router; from a client outside the network, each subnet, a private site
   through Traefik, the exit node, and SSH to the host's management address
   over Tailscale.
5. Remove the old node from the tailnet (admin console), so it can't come
   back and advertise the same subnets, then shut its VM down.

To roll back: stop this stack, point the two firewall routes back at the
old node, and start Tailscale there again.
