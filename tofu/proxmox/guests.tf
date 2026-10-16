# The Proxmox guests OpenTofu owns, by VMID. Each was created by hand first and
# came in through imports.tf; the values here match what they had then.
#
# What an API token can't set stays with Ansible (QUEUE.md): container
# features other than nesting, so `features` is set at creation and ignored
# afterwards. The provider has no setting for a container's timezone either.
#
# Each guest's host, name and addresses come from NetBox (netbox.tf); the
# networks' prefixes and gateways from TF_VAR_site.

locals {
  # The Pi-holes (community-scripts installs, unprivileged), one interface per
  # network they answer on.
  piholes = {
    "100" = { memory = 2048 }
    "109" = { memory = 1024 }
  }
  pihole_bridges = ["vmbr7", "vmbr0", "vmbr2", "vmbr6", "vmbr8"]

  # The Kubernetes nodes' networks (talos.tf): main, then management.
  k8s_bridges = ["vmbr7", "vmbr0"]

  bridges_with_addresses = toset(concat(
    local.k8s_bridges, local.pihole_bridges,
    flatten([for id, vm in local.docker_vms : vm.bridges if !contains(local.docker_vms_hand_built, id)]),
  ))

  # A container's address on a bridge: that network's prefix and the
  # container's last octet; the gateway where the network has one.
  addresses = {
    for id, _ in local.piholes : id => [
      for bridge in local.pihole_bridges : {
        address = "${var.site.networks[bridge].prefix}.${local.guest[id].host}/${var.site.networks[bridge].prefix_length}"
        gateway = var.site.networks[bridge].gateway
      }
    ]
  }

  # Plain Debian containers, by VMID: their size, networks and tags from NetBox
  # through netbox-guests.json (role "Plain container"), like the Talos nodes;
  # an entry: { cores, memory, disk_gb, bridges = ["vmbr7"], tags = [] }.
  containers = local.netbox_guests.containers

  guests = merge(
    { for id, _ in local.piholes : id => "pihole" },
    { for id, _ in local.containers : id => "container" },
    { for id, _ in local.docker_vms : id => "docker-vm" },
  )
}

# The template the plain containers start from, once, on the NAS's shared
# storage. Proxmox's own catalogue (pveam) lists it with this checksum.
resource "proxmox_download_file" "debian_13" {
  node_name           = local.guest["107"].node
  datastore_id        = "syn-nfs"
  content_type        = "vztmpl"
  url                 = "http://download.proxmox.com/images/system/debian-13-standard_13.6-1_amd64.tar.zst"
  checksum            = "4c0c27ca6ceab5ef0b84db57825a00f26157ef1854bafe97297813e1cbe8ecb8cc9c453cab6b3b0efe1ba193a50c47ece1e41d950e411b8730b835b71e9e754b"
  checksum_algorithm  = "sha512"
  overwrite           = false
  overwrite_unmanaged = true

  # On shared storage any host serves; the host it was asked of moving (HA)
  # is no reason to fetch it again.
  lifecycle {
    ignore_changes = [node_name]
  }
}

resource "proxmox_virtual_environment_container" "container" {
  for_each = local.containers

  node_name     = local.guest[each.key].node
  vm_id         = tonumber(each.key)
  unprivileged  = true
  start_on_boot = true
  started       = true
  tags          = each.value.tags

  cpu {
    cores = each.value.cores
  }

  memory {
    dedicated = each.value.memory
    swap      = 0
  }

  disk {
    datastore_id = "vms"
    size         = each.value.disk_gb
  }

  operating_system {
    template_file_id = proxmox_download_file.debian_13.id
    type             = "debian"
  }

  dynamic "network_interface" {
    for_each = each.value.bridges
    content {
      name   = "eth${network_interface.key}"
      bridge = network_interface.value
    }
  }

  initialization {
    hostname = local.guest[each.key].name

    dns {
      domain  = var.site.node_search_domain
      servers = var.site.node_dns_servers
    }

    dynamic "ip_config" {
      for_each = each.value.bridges
      content {
        ipv4 {
          address = "${var.site.networks[ip_config.value].prefix}.${local.guest[each.key].host}/${var.site.networks[ip_config.value].prefix_length}"
          gateway = var.site.networks[ip_config.value].gateway
        }
      }
    }
  }

  features {
    nesting = true
  }

  # A newer template is for the next container, not a reason to rebuild this one.
  lifecycle {
    ignore_changes = [operating_system]
  }
}

resource "proxmox_virtual_environment_container" "pihole" {
  for_each = local.piholes

  node_name     = local.guest[each.key].node
  vm_id         = tonumber(each.key)
  unprivileged  = true
  start_on_boot = true
  protection    = true
  tags          = ["adblock", "community-script"]

  cpu {
    cores = 2
  }

  memory {
    dedicated = each.value.memory
    swap      = 512
  }

  console {
    enabled   = true
    type      = "tty"
    tty_count = 2
  }

  disk {
    datastore_id = "vms"
    size         = 8
  }

  dynamic "network_interface" {
    for_each = local.pihole_bridges
    content {
      name   = "eth${network_interface.key}"
      bridge = network_interface.value
    }
  }

  initialization {
    hostname = local.guest[each.key].name

    dynamic "ip_config" {
      for_each = local.addresses[each.key]
      content {
        ipv4 {
          address = ip_config.value.address
          gateway = ip_config.value.gateway
        }
      }
    }
  }

  features {
    nesting = true
    keyctl  = true
  }

  lifecycle {
    prevent_destroy = true
    # The community-scripts installer's notes.
    ignore_changes = [features, operating_system, description]
  }
}
