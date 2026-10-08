# Everything specific to this site, kept out of git: a secret JSON value in
# Semaphore's environment (TF_VAR_site), whose master copy is a Vaultwarden
# note. guests.tf holds the rest, keyed by the same VMIDs and bridge names.
variable "site" {
  type = object({
    # Each bridge's network: the address prefix (the first three octets), and
    # the gateway for interfaces on it that have one.
    networks = map(object({
      prefix        = string
      prefix_length = optional(number, 24)
      gateway       = optional(string)
    }))
    # The Kubernetes nodes' resolvers and search domain.
    node_dns_servers   = list(string)
    node_search_domain = string
    guests = map(object({
      node     = string # the Proxmox host it runs on
      hostname = string
      host     = optional(number)           # a container's last octet, the same on every network
      macs     = optional(list(string), []) # the VM's NICs, in order
    }))
  })

  validation {
    condition     = length(setsubtract(keys(local.guests), keys(var.site.guests))) == 0
    error_message = "site.guests needs an entry for every VMID in guests.tf."
  }

  validation {
    condition     = length(setsubtract(local.bridges_with_addresses, keys(var.site.networks))) == 0
    error_message = "site.networks needs an entry for every bridge a container has an address on."
  }

  validation {
    condition     = alltrue([for id, kind in local.guests : kind == "docker-vm" || var.site.guests[id].host != null])
    error_message = "Every container in site.guests needs host (its last octet)."
  }

  validation {
    condition     = length(var.site.guests["107"].macs) == length(local.docker_vm_bridges)
    error_message = "site.guests[\"107\"].macs needs one MAC per Docker VM network device."
  }
}
