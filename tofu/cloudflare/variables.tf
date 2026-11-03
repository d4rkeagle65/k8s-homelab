variable "netbox_url" {
  description = "NetBox's address, https://netbox.<domain> (TF_VAR_netbox_url)."
  type        = string
}

variable "netbox_token" {
  description = "A read-only NetBox token, \"nbt_...\", with or without \"Bearer \" (TF_VAR_netbox_token)."
  type        = string
  sensitive   = true
}
