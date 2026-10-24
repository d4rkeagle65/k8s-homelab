# The tailnet's DNS and settings (QUEUE-tailscale.md). Each is adopted with an
# import block; the imports are no-ops once the state holds them. The policy file
# comes in the reorganisation (step 4), built from NetBox's tags and git.

import {
  to = tailscale_dns_configuration.this
  id = "dns_configuration"
}

import {
  to = tailscale_tailnet_settings.this
  id = "tailnet_settings"
}

# All of DNS in one resource; none of the separate tailscale_dns_* resources may
# be used beside it. Each zone NetBox tags tailscale-dns is split to both Pi-holes
# and searched; there are no global nameservers, so devices keep their own for
# everything else.
resource "tailscale_dns_configuration" "this" {
  magic_dns          = true
  override_local_dns = false
  search_paths       = local.dns_zones

  dynamic "split_dns" {
    for_each = local.dns_zones
    content {
      domain = split_dns.value
      dynamic "nameservers" {
        for_each = local.dns_resolvers
        content {
          address = nameservers.value
        }
      }
    }
  }
}

# The tailnet's settings, as they were on 2026-10-24. None of them names the
# site, so they are written here.
resource "tailscale_tailnet_settings" "this" {
  devices_approval_on                         = true
  devices_auto_updates_on                     = true
  devices_key_duration_days                   = 180
  users_approval_on                           = false
  users_role_allowed_to_join_external_tailnet = "admin"
  network_flow_logging_on                     = false
  posture_identity_collection_on              = true
  https_enabled                               = false
  regional_routing_on                         = false
  acls_externally_managed_on                  = false
}
