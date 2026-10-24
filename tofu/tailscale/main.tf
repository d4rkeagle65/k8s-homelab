# The tailnet's policy, DNS and settings (QUEUE-tailscale.md). Each is adopted
# from the tailnet as it was with an import block, so the first plan changes
# nothing; the imports are no-ops once the state holds them.

import {
  to = tailscale_acl.this
  id = "acl"
}

import {
  to = tailscale_dns_configuration.this
  id = "dns_configuration"
}

import {
  to = tailscale_tailnet_settings.this
  id = "tailnet_settings"
}

resource "tailscale_acl" "this" {
  acl = var.tailnet.policy
}

# All of DNS in one resource; none of the separate tailscale_dns_* resources may
# be used beside it.
resource "tailscale_dns_configuration" "this" {
  magic_dns          = var.tailnet.dns.magic_dns
  override_local_dns = var.tailnet.dns.override_local_dns
  search_paths       = var.tailnet.dns.search_paths

  dynamic "nameservers" {
    for_each = var.tailnet.dns.nameservers
    content {
      address = nameservers.value
    }
  }

  dynamic "split_dns" {
    for_each = var.tailnet.dns.split_dns
    content {
      domain = split_dns.key
      dynamic "nameservers" {
        for_each = split_dns.value
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
