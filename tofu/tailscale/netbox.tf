# What the tailnet's DNS and policy are built from, read from NetBox at every plan
# (QUEUE-tailscale.md): the zones tagged tailscale-dns and the Pi-holes (the VMs tagged
# pihole); the prefixes tagged tailscale-route-home or -travel; and the devices, VMs and
# IP addresses tagged tailscale-host. One
# GraphQL query with a read-only token; a plan stops when NetBox doesn't answer it in
# full, rather than plan from a partial list.
data "http" "netbox" {
  url    = "${trimsuffix(var.netbox_url, "/")}/graphql/"
  method = "POST"
  request_headers = {
    Authorization = "Bearer ${trimprefix(trimspace(var.netbox_token), "Bearer ")}"
    Content-Type  = "application/json"
    Accept        = "application/json"
  }
  request_body = jsonencode({
    query = "{ netbox_dns_zone_list { name tags { slug } } prefix_list { prefix description tags { slug } } device_list { name status role { name } tags { slug } interfaces { ip_addresses { address } } } virtual_machine_list { name status role { name } tags { slug } interfaces { name ip_addresses { address } } } ip_address_list { address description tags { slug } } }"
  })
  lifecycle {
    postcondition {
      condition     = self.status_code == 200 && can(jsondecode(self.response_body).data.netbox_dns_zone_list) && !can(jsondecode(self.response_body).errors)
      error_message = "NetBox's GraphQL didn't answer with the zones and VMs (HTTP ${self.status_code}): check TF_VAR_netbox_url and TF_VAR_netbox_token."
    }
  }
}

locals {
  netbox = jsondecode(data.http.netbox.response_body).data

  # A device or VM the tailnet routes to or resolves through only while it's in use
  # (Active, Staged): the root README's "What a NetBox status means".
  in_use = ["active", "staged"]

  # The zones the tailnet resolves through the Pi-holes, and searches.
  dns_zones = sort([
    for z in local.netbox.netbox_dns_zone_list : z.name if contains([for t in z.tags : t.slug], "tailscale-dns")
  ])

  # Each Pi-hole's address on net0, the main (production) network: the one tailnet
  # clients reach through the home subnet router.
  dns_resolvers = sort(flatten([
    for vm in local.netbox.virtual_machine_list : [
      for i in vm.interfaces : [for a in i.ip_addresses : split("/", a.address)[0]] if i.name == "net0"
    ] if contains([for t in vm.tags : t.slug], "pihole") && contains(local.in_use, lower(vm.status))
  ]))
}

check "netbox_dns" {
  assert {
    condition     = length(local.dns_zones) > 0 && length(local.dns_resolvers) > 0
    error_message = "NetBox has no zone tagged tailscale-dns or no Pi-hole with a net0 address: the tailnet's DNS would be emptied."
  }
}
