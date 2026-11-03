resource "cloudflare_dns_record" "this" {
  for_each = local.records

  zone_id = local.zones[each.value.zone]
  name    = each.value.name
  type    = each.value.type
  ttl     = each.value.ttl
  proxied = each.value.proxied
  content = each.value.content
  # Cloudflare keeps an SRV record's priority at the top as well as in data.
  priority = each.value.type == "SRV" ? each.value.data.priority : each.value.priority
  data     = each.value.data
}
