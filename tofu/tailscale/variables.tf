# What names the site, as one JSON value (TF_VAR_tailnet), so it stays out of git
# (README.md): its master copy is a Vaultwarden note, "OpenTofu site (Tailscale)".
#   policy  the tailnet policy file, HuJSON, exactly as the admin console holds it
#   dns     search paths, and each split-DNS domain with its nameservers' addresses
#
# The policy moves to policy.tf, built from NetBox's tags and git, in the
# reorganisation (QUEUE-tailscale.md, step 4); until then it is adopted as it is.
variable "tailnet" {
  type = object({
    policy = string
    dns = object({
      magic_dns          = optional(bool, true)
      override_local_dns = optional(bool, false)
      nameservers        = optional(list(string), [])
      search_paths       = list(string)
      split_dns          = map(list(string))
    })
  })
  sensitive = true
}
