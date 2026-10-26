# The tailnet policy file (QUEUE-tailscale.md, step 4). Its structure (who may reach
# what, tags, SSH, tests) is here; every address comes from NetBox at plan time, so
# git names no subnet, host or person.
#
#   hosts      the prefixes tagged tailscale-route-home or -travel, as net-<name>
#   ipsets     ipset:svc-<NetBox role>: the addresses on routed networks of every
#              device, VM or IP address tagged tailscale-host with that role (an IP
#              address's "role" is the last part of its description, so Traefik's
#              LoadBalancer address is ipset:svc-traefik). Grants name roles, never hosts.
#   users      no email is in git: what an invited user may reach is granted to
#              autogroup:member; the owner reaches everything.

locals {

  # A prefix's short name: the names the owner chose (2026-10-24), else net-<slug>.
  net_names = {
    "management"        = "net-mgmt"
    "production"        = "net-prod"
    "dmz"               = "net-dmz"
    "iot"               = "net-iot"
    "travel router lan" = "net-tr"
  }
  routed = {
    for p in local.netbox.prefix_list :
    lookup(local.net_names, lower(p.description), "net-${replace(lower(p.description), "/[^a-z0-9]+/", "-")}") => {
      cidr   = p.prefix
      router = contains([for t in p.tags : t.slug], "tailscale-route-home") ? "home" : "travel"
    } if length(setintersection([for t in p.tags : t.slug], ["tailscale-route-home", "tailscale-route-travel"])) > 0
  }
  home_nets   = sort([for n, v in local.routed : n if v.router == "home"])
  travel_nets = sort([for n, v in local.routed : n if v.router == "travel"])

  # Every tailscale-host object in use (local.in_use) as {role, addresses}, its addresses kept only where a
  # routed network holds them (a Pi-hole's Work laptop leg isn't reachable anyway).
  host_objects = concat(
    [for d in local.netbox.device_list : {
      role      = d.role.name
      addresses = flatten([for i in d.interfaces : [for a in i.ip_addresses : a.address]])
    } if contains([for t in d.tags : t.slug], "tailscale-host") && contains(local.in_use, lower(d.status))],
    [for v in local.netbox.virtual_machine_list : {
      role      = v.role.name
      addresses = flatten([for i in v.interfaces : [for a in i.ip_addresses : a.address]])
    } if contains([for t in v.tags : t.slug], "tailscale-host") && contains(local.in_use, lower(v.status))],
    [for a in local.netbox.ip_address_list : {
      role      = element(split("/", a.description), length(split("/", a.description)) - 1)
      addresses = [a.address]
    } if contains([for t in a.tags : t.slug], "tailscale-host")],
  )
  svc_ipsets = {
    for role in distinct([for o in local.host_objects : o.role]) :
    "ipset:svc-${trim(replace(lower(role), "/[^a-z0-9]+/", "-"), "-")}" => sort(distinct(flatten([
      for o in local.host_objects : [
        for a in o.addresses : "add ${split("/", a)[0]}"
        if anytrue([for n in values(local.routed) : can(cidrnetmask(n.cidr)) && cidrhost(n.cidr, 0) == cidrhost("${split("/", a)[0]}/${split("/", n.cidr)[1]}", 0)])
      ] if o.role == role
    ])))
  }

  # A test address in a network: its first host.
  t = { for n, v in local.routed : n => cidrhost(v.cidr, 1) }

  policy = {
    hosts  = { for n, v in local.routed : n => v.cidr }
    ipsets = local.svc_ipsets

    # The travel router still carries tag:dev-travelrouter: both names stay until it's
    # retagged tag:travel-router, then the old one goes (QUEUE-tailscale.md).
    tagOwners = {
      "tag:subnet-router"    = ["autogroup:admin"]
      "tag:travel-router"    = ["autogroup:admin"]
      "tag:dev-travelrouter" = ["autogroup:admin"]
    }

    autoApprovers = {
      routes = merge(
        { for n in local.home_nets : local.routed[n].cidr => ["tag:subnet-router"] },
        { for n in local.travel_nets : local.routed[n].cidr => ["tag:travel-router", "tag:dev-travelrouter"] },
      )
      exitNode = ["tag:subnet-router"]
    }

    grants = concat(
      # The owner: everything.
      [{ src = ["autogroup:owner"], dst = ["*"], ip = ["*"] }],
      # An invited user: DNS, the apps behind Traefik, and Vaultwarden on the Docker VM.
      [
        { src = ["autogroup:member"], dst = ["ipset:svc-pi-hole"], ip = ["udp:53", "tcp:53"] },
        { src = ["autogroup:member"], dst = ["ipset:svc-traefik"], ip = ["tcp:80", "tcp:443"] },
        { src = ["autogroup:member"], dst = ["ipset:svc-docker-vm"], ip = ["tcp:443"] },
      ],
      # The travel router's LAN and the home networks reach each other.
      [
        {
          src = concat(["tag:travel-router", "tag:dev-travelrouter"], [for n in local.travel_nets : "host:${n}"])
          dst = ["host:net-prod", "host:net-dmz"]
          ip  = ["*"]
        },
        {
          src = ["host:net-mgmt", "host:net-prod", "host:net-dmz"]
          dst = concat(["tag:travel-router", "tag:dev-travelrouter"], [for n in local.travel_nets : "host:${n}"])
          ip  = ["*"]
        },
      ],
      # The home router (the Docker VM's Tailscale) monitors and reaches the travel LAN.
      [{ src = ["tag:subnet-router"], dst = [for n in local.travel_nets : "host:${n}"], ip = ["tcp:22", "tcp:9100"] }],
      # Peer relay through the home router.
      [{
        src = [for n in concat(local.home_nets, local.travel_nets) : "host:${n}"]
        dst = ["tag:subnet-router"]
        app = { "tailscale.com/cap/relay" = [] }
      }],
    )

    ssh = [
      { action = "check", src = ["autogroup:member"], dst = ["autogroup:self"], users = ["autogroup:nonroot", "root"] },
      { action = "accept", src = ["autogroup:admin"], dst = ["tag:travel-router", "tag:dev-travelrouter"], users = ["autogroup:nonroot", "root"] },
    ]

    # Checked by Tailscale on every save: a change that breaks one is refused.
    tests = [
      {
        src    = "tag:travel-router"
        accept = ["${local.t["net-prod"]}:443", "${local.t["net-dmz"]}:443"]
        deny   = ["${local.t["net-mgmt"]}:22", "${local.t["net-iot"]}:443"]
      },
      {
        src    = "tag:subnet-router"
        accept = [for n in local.travel_nets : "${local.t[n]}:9100"]
        deny   = concat([for n in local.travel_nets : "${local.t[n]}:443"], ["${local.t["net-mgmt"]}:22"])
      },
    ]
  }
}

check "netbox_policy" {
  assert {
    condition     = alltrue([for n in ["net-mgmt", "net-prod", "net-dmz", "net-iot"] : contains(local.home_nets, n)]) && length(local.travel_nets) == 1
    error_message = "NetBox's tailscale-route tags don't give the four home networks and one travel network the policy expects."
  }
  assert {
    condition     = alltrue([for s in ["ipset:svc-pi-hole", "ipset:svc-traefik", "ipset:svc-docker-vm"] : length(lookup(local.svc_ipsets, s, [])) > 0])
    error_message = "A role the grants name (Pi-hole, traefik, Docker VM) has no tailscale-host object with a routed address in NetBox."
  }
}

import {
  to = tailscale_acl.this
  id = "acl"
}

resource "tailscale_acl" "this" {
  acl = jsonencode(local.policy)
}
