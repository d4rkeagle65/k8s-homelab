# The Proxmox guests OpenTofu owns, by VMID. Each was created by hand first and
# came in through imports.tf; the values here match what they had then.
#
# What an API token can't set stays with Ansible (QUEUE.md): the raw lxc.*
# lines (the k8s nodes run apparmor-unconfined with /proc and /sys writable)
# and container features other than nesting, so `features` is set at creation
# and ignored afterwards. The provider has no setting for a container's
# timezone either.

locals {
  # The Kubernetes nodes: privileged containers on ZFS, eth0 on the main
  # network (default route), eth1 on the management network.
  k8s_roles = {
    control = { memory = 4096, storage_gb = 32, local_db_gb = 0, tags = ["k8s", "k8s-control"] }
    worker  = { memory = 8192, storage_gb = 48, local_db_gb = 128, tags = ["k8s", "k8s-worker"] }
  }
  k8s_nodes = {
    "101" = "control"
    "102" = "control"
    "103" = "control"
    "104" = "worker"
    "105" = "worker"
    "106" = "worker"
  }

  # The Pi-holes (community-scripts installs, unprivileged), one interface per
  # network they answer on.
  piholes = {
    "100" = { memory = 2048 }
    "109" = { memory = 1024 }
  }
  pihole_bridges = ["vmbr7", "vmbr0", "vmbr2", "vmbr6", "vmbr8"]

  guests = merge(
    { for id, role in local.k8s_nodes : id => "k8s" },
    { for id, _ in local.piholes : id => "pihole" },
    { "107" = "docker-vm" },
  )
}

resource "proxmox_virtual_environment_container" "k8s_node" {
  for_each = local.k8s_nodes

  node_name     = var.site.guests[each.key].node
  vm_id         = tonumber(each.key)
  unprivileged  = false
  start_on_boot = true
  tags          = local.k8s_roles[each.value].tags

  cpu {
    cores = 2
  }

  memory {
    dedicated = local.k8s_roles[each.value].memory
    swap      = 0
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

  # CRI-O's image and container storage.
  mount_point {
    volume = "vms:subvol-${each.key}-disk-1"
    path   = "/var/lib/containers/storage/"
    size   = "${local.k8s_roles[each.value].storage_gb}G"
    backup = true
  }

  # Workers: the local-db StorageClass's data (CloudNativePG).
  dynamic "mount_point" {
    for_each = local.k8s_roles[each.value].local_db_gb > 0 ? [1] : []
    content {
      volume = "vms:subvol-${each.key}-disk-2"
      path   = "/opt/local-path-provisioner"
      size   = "${local.k8s_roles[each.value].local_db_gb}G"
      backup = true
    }
  }

  network_interface {
    name        = "eth0"
    bridge      = "vmbr7"
    firewall    = false
    mac_address = var.site.guests[each.key].interfaces[0].mac
  }

  network_interface {
    name        = "eth1"
    bridge      = "vmbr0"
    mac_address = var.site.guests[each.key].interfaces[1].mac
  }

  initialization {
    hostname = var.site.guests[each.key].hostname

    dns {
      domain  = var.site.node_search_domain
      servers = var.site.node_dns_servers
    }

    dynamic "ip_config" {
      for_each = var.site.guests[each.key].interfaces
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
    mount   = ["nfs"]
  }

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [features, operating_system]
  }
}

resource "proxmox_virtual_environment_container" "pihole" {
  for_each = local.piholes

  node_name     = var.site.guests[each.key].node
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
      name        = "eth${network_interface.key}"
      bridge      = network_interface.value
      mac_address = var.site.guests[each.key].interfaces[network_interface.key].mac
    }
  }

  initialization {
    hostname = var.site.guests[each.key].hostname

    dynamic "ip_config" {
      for_each = var.site.guests[each.key].interfaces
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

# The Docker host: Semaphore, Dockhand, Kea, Tailscale and Vaultwarden.
resource "proxmox_virtual_environment_vm" "docker_vm" {
  node_name = var.site.guests["107"].node
  vm_id     = 107
  name      = var.site.guests["107"].hostname
  on_boot   = true
  tags      = ["community-script", "debian13"]

  bios          = "ovmf"
  machine       = "q35"
  scsi_hardware = "virtio-scsi-pci"
  tablet_device = false

  agent {
    enabled = true
  }

  cpu {
    type    = "x86-64-v2-AES"
    sockets = 2
    cores   = 2
  }

  memory {
    dedicated = 8192
    floating  = 0
  }

  efi_disk {
    datastore_id = "vms"
  }

  disk {
    interface    = "scsi0"
    datastore_id = "vms"
    size         = 32
    discard      = "on"
    ssd          = true
  }

  # /opt: the stacks' data.
  disk {
    interface    = "scsi1"
    datastore_id = "vms"
    size         = 64
    discard      = "on"
    ssd          = true
  }

  # Management, main and DMZ networks, in that order.
  dynamic "network_device" {
    for_each = ["vmbr0", "vmbr7", "vmbr8"]
    content {
      bridge      = network_device.value
      mac_address = var.site.guests["107"].interfaces[network_device.key].mac
    }
  }

  operating_system {
    type = "l26"
  }

  serial_device {}

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [description]
  }
}
