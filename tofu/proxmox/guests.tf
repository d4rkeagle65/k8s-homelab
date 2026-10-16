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
  k8s_bridges       = ["vmbr7", "vmbr0"]
  docker_vm_bridges = ["vmbr0", "vmbr7", "vmbr8"] # management, main, DMZ

  bridges_with_addresses = toset(concat(local.k8s_bridges, local.pihole_bridges))

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

  guests = merge(
    { for id, _ in local.piholes : id => "pihole" },
    { "107" = "docker-vm" },
  )
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

# The Docker host: Semaphore, Dockhand, Kea, Tailscale and Vaultwarden.
resource "proxmox_virtual_environment_vm" "docker_vm" {
  node_name = local.guest["107"].node
  vm_id     = 107
  name      = local.guest["107"].name
  on_boot   = true
  tags      = ["community-script", "debian13"]

  # Semaphore, and with it this run, lives on this VM: a change that needs a
  # reboot only warns, and the reboot is done by hand. Every update to this
  # VM also sends its name to Proxmox, changed or not, so the token needs
  # VM.Config.Options here even for a change that is otherwise state-only.
  reboot_after_update = false

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

  # The balloon device on with its minimum at the full size: the VM never gives
  # memory back, and the guest reports its real use to Proxmox. Without the
  # device (floating = 0) Proxmox counts every page the guest has touched,
  # file cache included, and shows the VM near 100% used however idle it is.
  memory {
    dedicated = 12288
    floating  = 12288
  }

  # Every disk on the NAS (syn-nfs, shared), so Proxmox HA can restart the VM
  # on another host. Moved there live with qm disk move (QUEUE-talos.md, Phase
  # 1); applying a different datastore here would move or recreate them.
  efi_disk {
    datastore_id = "syn-nfs"
    file_format  = "raw"
  }

  disk {
    interface    = "scsi0"
    datastore_id = "syn-nfs"
    file_format  = "raw"
    size         = 32
    discard      = "on"
    ssd          = true
  }

  # /opt: the stacks' data.
  disk {
    interface    = "scsi1"
    datastore_id = "syn-nfs"
    file_format  = "raw"
    size         = 64
    discard      = "on"
    ssd          = true
  }

  # The VM's network devices are one list attribute, so each MAC is stated:
  # left out, it would read as a change.
  dynamic "network_device" {
    for_each = local.docker_vm_bridges
    content {
      bridge      = network_device.value
      mac_address = local.guest["107"].macs[network_device.key]
    }
  }

  operating_system {
    type = "l26"
  }

  serial_device {}

  # HA moves it between hosts; the host is NetBox's (netbox.tf), recorded by
  # the nightly sync, so a move is never a change here.
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [description, node_name]
  }
}
