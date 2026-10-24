# NetBox, which the tailnet's DNS is built from (netbox.tf). Both come from Semaphore's
# environment; the token is Key Vault's NETBOX-READ-TOKEN. Nothing about the site is
# stored for this module: what names it is read from NetBox at every plan.
variable "netbox_url" {
  description = "NetBox's address, https://netbox.<domain> (TF_VAR_netbox_url)."
  type        = string
}

variable "netbox_token" {
  description = "A read-only NetBox token, \"nbt_...\", with or without \"Bearer \" (TF_VAR_netbox_token)."
  type        = string
  sensitive   = true
}
