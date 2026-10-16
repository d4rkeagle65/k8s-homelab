# Each guest's host, name, addresses and MACs, read from NetBox at every plan:
# NetBox owns them (scripts/netbox_seed.py keeps them in step with Proxmox every
# night), so TF_VAR_site keeps only what NetBox doesn't hold. One GraphQL
# query through NetBox's route, with a read-only token; a plan stops when NetBox
# doesn't answer it in full, rather than plan from a partial list.

variable "netbox_url" {
  description = "NetBox's address, https://netbox.<domain> (TF_VAR_netbox_url)."
  type        = string
}

variable "netbox_token" {
  description = "A read-only NetBox token, \"nbt_...\", with or without \"Bearer \" (TF_VAR_netbox_token)."
  type        = string
  sensitive   = true
}

data "http" "netbox_vms" {
  url    = "${trimsuffix(var.netbox_url, "/")}/graphql/"
  method = "POST"
  request_headers = {
    Authorization = "Bearer ${trimprefix(trimspace(var.netbox_token), "Bearer ")}"
    Content-Type  = "application/json"
    Accept        = "application/json"
  }
  request_body = jsonencode({
    query = "{ virtual_machine_list { name custom_fields device { name } interfaces { name primary_mac_address { mac_address } ip_addresses { address } } } }"
  })

  lifecycle {
    postcondition {
      condition     = self.status_code == 200 && can(jsondecode(self.response_body).data.virtual_machine_list) && !can(jsondecode(self.response_body).errors)
      error_message = "NetBox's GraphQL didn't answer with the VM list (HTTP ${self.status_code}): check TF_VAR_netbox_url and TF_VAR_netbox_token."
    }
  }
}

locals {
  # The Talos VMs' and plain containers' shape, which NetBox owns: generated
  # from it by scripts/netbox_guests.py and merged as a pull request, so a plan
  # shows only a change someone reviewed. Never edited by hand.
  netbox_guests = jsondecode(file("${path.module}/netbox-guests.json"))

  netbox_vms = {
    for vm in jsondecode(data.http.netbox_vms.response_body).data.virtual_machine_list :
    tostring(vm.custom_fields.vmid) => vm if try(vm.custom_fields.vmid, null) != null
  }
  # Each VM's IPv4 addresses, without their length, and its MACs by interface.
  netbox_addresses = {
    for id, vm in local.netbox_vms : id => flatten([
      for i in vm.interfaces : [for a in i.ip_addresses : split("/", a.address)[0]]
    ])
  }
  netbox_macs = {
    for id, vm in local.netbox_vms : id => { for i in vm.interfaces : i.name => try(i.primary_mac_address.mac_address, null) }
  }

  # By VMID (NetBox's custom field vmid): the Proxmox host (the VM's device),
  # the name, the MACs in net0, net1... order, and the last octet of its address
  # on the main network (vmbr7), which a container and a Talos node use on
  # every network.
  guest = {
    for id, vm in local.netbox_vms : id => {
      node = try(vm.device.name, null)
      name = vm.name
      macs = [for n in range(length(local.netbox_macs[id])) : lookup(local.netbox_macs[id], "net${n}", null)]
      host = try(split(".", one([
        for a in local.netbox_addresses[id] : a if startswith(a, "${var.site.networks["vmbr7"].prefix}.")
      ]))[3], null)
    }
  }

  # Every VMID this module builds or imports needs NetBox's node, name and
  # main-network address for it.
  guests_needing_netbox = setunion(keys(local.guests), keys(local.talos_nodes))
}

check "netbox_has_every_guest" {
  assert {
    condition = alltrue([
      for id in local.guests_needing_netbox :
      try(local.guest[id].node != null && local.guest[id].name != "" && local.guest[id].host != null, false)
    ])
    error_message = "NetBox lacks a node, name or vmbr7 address for VMID(s) ${join(", ", [for id in local.guests_needing_netbox : id if !try(local.guest[id].node != null && local.guest[id].host != null, false)])}: run scripts/netbox_seed.py."
  }

  assert {
    condition = alltrue([
      for id in local.docker_vms_hand_built :
      try(length(compact(local.guest[id].macs)) == length(local.docker_vms[id].bridges), false)
    ])
    error_message = "NetBox needs one MAC per network device of each hand-built Docker VM (${join(", ", local.docker_vms_hand_built)}): they were made with those MACs."
  }
}
