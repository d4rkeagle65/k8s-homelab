resource "cloudflare_dns_record" "this" {
  for_each = var.dns.records

  zone_id = var.dns.zones[each.value.zone]
  name    = each.value.name
  type    = each.value.type
  ttl     = each.value.ttl
  proxied = each.value.proxied
  content = each.value.content
  # Cloudflare keeps an SRV record's priority at the top as well as in data.
  priority = each.value.type == "SRV" ? each.value.data.priority : each.value.priority
  comment  = each.value.comment
  data     = each.value.data
}
