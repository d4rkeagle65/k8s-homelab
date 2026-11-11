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
#
# The policy is edited only through policy.tf: the admin console's editor is
# locked and links here. In an emergency, switch the lock off in the console
# (Settings, General, policy file management), edit, then make the same change
# in a pull request; the next apply otherwise restores both the lock and
# policy.tf's policy. The API (and so OpenTofu) is never locked.
resource "tailscale_tailnet_settings" "this" {
  devices_approval_on                         = true
  devices_auto_updates_on                     = true
  devices_key_duration_days                   = 180
  users_approval_on                           = false
  users_role_allowed_to_join_external_tailnet = "admin"
  network_flow_logging_on                     = false
  posture_identity_collection_on              = true
  # On for Tailscale Funnel (ManicTime's public HTTPS endpoint) and MagicDNS
  # HTTPS certificates. The OpenTofu OAuth client can't toggle httpsEnabled --
  # Tailscale gates it to a human admin ("calling actor does not have enough
  # permissions", #591 tasks 1694/1695), and the client even reads it back as
  # false -- so the owner enables it in the admin console (DNS page) and the
  # ignore_changes below keeps a failed PATCH from aborting every apply. The
  # value here documents the intended state. See docker/tailscale and QUEUE.md.
  https_enabled                               = true
  regional_routing_on                         = false
  acls_externally_managed_on                  = true
  acls_external_link                          = "https://github.com/d4rkeagle65/k8s-homelab/blob/main/tofu/tailscale/policy.tf"

  # httpsEnabled is owner-managed in the console (see above): never let tofu try
  # to change it, or the whole tailscale apply fails on a permission error.
  lifecycle {
    ignore_changes = [https_enabled]
  }
}
