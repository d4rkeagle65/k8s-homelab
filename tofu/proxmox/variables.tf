# Everything specific to this site, kept out of git (a secret JSON value in
# Semaphore's environment, TF_VAR_site, with a copy in Vaultwarden): which
# Proxmox host each guest runs on, its host name, and each network interface's
# MAC and address, in interface order (eth0, eth1, ...). guests.tf holds the
# rest, keyed by the same VMIDs.
variable "site" {
  type = object({
    # The k8s nodes' resolvers and search domain.
    node_dns_servers   = list(string)
    node_search_domain = string
    guests = map(object({
      node     = string
      hostname = string
      interfaces = list(object({
        mac     = string
        address = optional(string) # CIDR; containers only
        gateway = optional(string)
      }))
    }))
  })

  validation {
    condition     = length(setsubtract(keys(local.guests), keys(var.site.guests))) == 0
    error_message = "site.guests needs an entry for every VMID in guests.tf."
  }
}
