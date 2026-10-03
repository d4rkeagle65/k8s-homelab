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

3. **The firewall** must:
   - let the host reach every advertised subnet (it reaches the subnets
     it isn't on through the default gateway);
   - route 100.64.0.0/10 to the host. The subnet router doesn't source-NAT,
     so LAN hosts answer tailnet clients' own addresses through the
     firewall, which hands them back to the router;
   - route the subnets other tailnet routers advertise (accepted with
     `--accept-routes`, e.g. a travel router's network) to the host, so LAN
     devices reach them through it;
   - forward UDP 41641 to the host, for direct connections.

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

The firewall's 100.64.0.0/10 route has one target, so the old and new
nodes can't both serve the subnets at once: this is a short cutover, not a
side-by-side run.

1. Create an auth key (admin console, Settings > Keys; not reusable). Set the
   stack's variables in Dockhand, with `TS_AUTHKEY` marked secret, and
   deploy. Approve the new node's exit node in the admin console and
   disable key expiry for it, but leave its subnet routes unapproved.
2. Disable the old node's subnet routes and exit node, approve the new
   node's routes, and point the firewall's 100.64.0.0/10 route, its routes
   to other tailnet routers' subnets, and the UDP 41641 forward at the host.
3. Test from a client outside the network: each subnet, a private site
   through Traefik, the exit node, and SSH to the host's management address
   over Tailscale. From the LAN, test a device behind another tailnet
   router.
4. Shut the old VM down and remove it from the tailnet. To roll back,
   reverse step 2.
