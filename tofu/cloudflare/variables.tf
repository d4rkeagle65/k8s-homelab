# Every record this module owns, as one JSON value (TF_VAR_dns): the names and
# contents are the site's, so they stay out of git (README.md).
#   zones    each zone's name and Cloudflare id
#   records  keyed r01, r02, ... (meaningless on purpose, so git names nothing):
#            the zone's name, Cloudflare's record id (from its import; for a new
#            record, any placeholder until it's created), and the record. A new
#            record takes the next free key.
#
# Not in it, each with one owner already: the records the Cloudflare tunnel's
# ingress controller makes for the tunnel Ingresses (their comment says
# "managed by cloudflare-tunnel-ingress-controller"), the tunnel's public
# hostnames, and cert-manager's temporary _acme-challenge records.
variable "dns" {
  type = object({
    zones = map(string)
    records = map(object({
      zone     = string
      id       = string
      name     = string
      type     = string
      ttl      = optional(number, 1)
      proxied  = optional(bool, false)
      content  = optional(string)
      priority = optional(number)
      comment  = optional(string)
      data = optional(object({
        priority = number
        weight   = number
        port     = number
        target   = string
      }))
    }))
  })

  validation {
    condition     = alltrue([for r in values(var.dns.records) : contains(keys(var.dns.zones), r.zone)])
    error_message = "Every record's zone must be one of zones."
  }

  validation {
    condition = alltrue([
      for r in values(var.dns.records) :
      !can(regex("cloudflare-tunnel-ingress-controller", coalesce(r.comment, "-"))) && !startswith(r.name, "_acme-challenge")
    ])
    error_message = "TF_VAR_dns lists a record the tunnel controller or cert-manager owns; leave those out."
  }

  validation {
    condition     = alltrue([for r in values(var.dns.records) : r.type == "SRV" ? r.data != null : r.content != null])
    error_message = "Every record needs content, or data for an SRV record."
  }
}
