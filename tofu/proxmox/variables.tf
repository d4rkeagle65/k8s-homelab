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
    # The cluster's values the Talos configs need (talos-config.tf): the API
    # address as host:port, without https://, and kubeadm-config's networking.
    talos = object({
      cluster_endpoint = string
      dns_domain       = string
      pod_subnet       = string
      service_subnet   = string
      # The API address the control planes share (Layer2VIPConfig), the one
      # cluster_endpoint's name resolves to. Unset, they share none (kube-vip
      # on the kubeadm control planes holds it until the handover).
      api_vip = optional(string)
    })
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
    condition     = alltrue([for id, _ in local.talos_nodes : try(var.site.guests[id].host != null, false)])
    error_message = "site.guests needs an entry, with host (its last octet), for every Talos node in talos-config.tf."
  }

  validation {
    condition     = length(var.site.guests["107"].macs) == length(local.docker_vm_bridges)
    error_message = "site.guests[\"107\"].macs needs one MAC per Docker VM network device."
  }
}

# The cluster's Talos secrets: the base64 of secrets.yaml (TALOS_SECRETS in
# Vaultwarden), a secret in Semaphore's environment as TF_VAR_talos_secrets.
variable "talos_secrets" {
  type      = string
  sensitive = true

  validation {
    condition     = can(yamldecode(base64decode(var.talos_secrets)).certs.os.key) && can(yamldecode(base64decode(var.talos_secrets)).secrets.secretboxencryptionsecret)
    error_message = "talos_secrets isn't the base64 of a Talos secrets.yaml (with a secretbox encryption secret)."
  }
}
