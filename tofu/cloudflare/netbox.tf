# Every record this module owns, read from NetBox at every plan (QUEUE-site-facts.md): the
# active zones tagged cloudflare outside NetBox DNS's default view (the public view, whose
# zones share names with the local ones), each zone's Cloudflare ID in its custom field
# cloudflare_zone_id, and their active records that NetBox DNS doesn't manage itself (it
# makes each zone's NS and SOA). A record's key is its NetBox ID; whether Cloudflare proxies it
# is its custom field cloudflare_proxied. One GraphQL query with a read-only token; a plan
# stops when NetBox doesn't answer it in full, rather than plan from a partial list.
#
# Not in NetBox, each with one owner already: the records the Cloudflare tunnel's ingress
# controller makes for the tunnel Ingresses (their comment says "managed by
# cloudflare-tunnel-ingress-controller"), the tunnel's public hostnames, and cert-manager's
# temporary _acme-challenge records.
data "http" "netbox" {
  url    = "${trimsuffix(var.netbox_url, "/")}/graphql/"
  method = "POST"
  request_headers = {
    Authorization = "Bearer ${trimprefix(trimspace(var.netbox_token), "Bearer ")}"
    Content-Type  = "application/json"
    Accept        = "application/json"
  }
  request_body = jsonencode({
    query = "{ netbox_dns_zone_list { id name status custom_fields tags { slug } view { default_view } } netbox_dns_record_list { id name type value ttl status managed custom_fields zone { id } } }"
  })
  lifecycle {
    postcondition {
      condition     = self.status_code == 200 && can(jsondecode(self.response_body).data.netbox_dns_record_list) && !can(jsondecode(self.response_body).errors)
      error_message = "NetBox's GraphQL didn't answer with the zones and records (HTTP ${self.status_code}): check TF_VAR_netbox_url and TF_VAR_netbox_token."
    }
  }
}

locals {
  netbox = jsondecode(data.http.netbox.response_body).data

  # The public zones, by NetBox ID: their names and Cloudflare IDs.
  public_zones = {
    for z in local.netbox.netbox_dns_zone_list : z.id => {
      name    = trimsuffix(z.name, ".")
      zone_id = try(z.custom_fields.cloudflare_zone_id, null)
    } if contains([for t in z.tags : t.slug], "cloudflare") && !z.view.default_view && lower(z.status) == "active"
  }

  zones = { for z in values(local.public_zones) : z.name => z.zone_id }

  # NetBox DNS keeps a record's value as the zone file has it; Cloudflare wants an MX's
  # priority and an SRV's parts apart, and names without the trailing dot.
  records = {
    for r in local.netbox.netbox_dns_record_list : r.id => {
      zone     = local.public_zones[r.zone.id].name
      name     = r.name == "@" ? local.public_zones[r.zone.id].name : "${r.name}.${local.public_zones[r.zone.id].name}"
      type     = r.type
      ttl      = coalesce(r.ttl, 1)
      proxied  = try(r.custom_fields.cloudflare_proxied, false) == true
      content  = r.type == "SRV" ? null : r.type == "MX" ? trimsuffix(split(" ", r.value)[1], ".") : r.type == "CNAME" ? trimsuffix(r.value, ".") : r.value
      priority = r.type == "MX" ? tonumber(split(" ", r.value)[0]) : null
      data = r.type != "SRV" ? null : {
        priority = tonumber(split(" ", r.value)[0])
        weight   = tonumber(split(" ", r.value)[1])
        port     = tonumber(split(" ", r.value)[2])
        target   = trimsuffix(split(" ", r.value)[3], ".")
      }
    } if contains(keys(local.public_zones), r.zone.id) && !r.managed && lower(r.status) == "active"
  }
}

check "netbox_cloudflare" {
  assert {
    condition     = length(local.zones) > 0 && alltrue([for id in values(local.zones) : id != null && id != ""])
    error_message = "NetBox has no public zone tagged cloudflare, or one without its cloudflare_zone_id."
  }
  assert {
    condition     = alltrue([for r in values(local.records) : !startswith(r.name, "_acme-challenge")])
    error_message = "A NetBox record is a cert-manager challenge, which cert-manager owns; leave it out of NetBox."
  }
}
