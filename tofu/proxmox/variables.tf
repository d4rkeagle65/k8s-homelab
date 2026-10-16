# What's specific to this site and NetBox doesn't hold, kept out of git: a
# secret JSON value in Semaphore's environment (TF_VAR_site), whose master copy
# is a Vaultwarden note. Each guest's host, name and addresses are NetBox's
# (netbox.tf); a guests key left in the value is ignored.
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
    # The cluster's values the Talos configs need (talos-config.tf): the API
    # address as host:port, without https://, and kubeadm-config's networking.
    talos = object({
      cluster_endpoint = string
      dns_domain       = string
      pod_subnet       = string
      service_subnet   = string
      # The API address the control planes share (Layer2VIPConfig), the one
      # cluster_endpoint's name resolves to. Unset, they share none.
      api_vip = optional(string)
    })
  })

  validation {
    condition     = length(setsubtract(local.bridges_with_addresses, keys(var.site.networks))) == 0
    error_message = "site.networks needs an entry for every bridge a container has an address on."
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
