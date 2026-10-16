# The Docker hosts: Debian VMs running Docker, their stacks deployed by Dockhand
# (docker/). Their shape is NetBox's (role "Docker VM"), through the generated
# netbox-guests.json; what's the same for every one of them is here.
#
# A new one is built from Debian's cloud image: cloud-init sets its addresses
# (each network's prefix and its last octet), the login in site.vm_login, and
# installs the QEMU agent (the provider waits on it). baseline.yml and Docker
# follow, as on any Debian host. The first one (107) was built by hand and
# imported: it has no image and no cloud-init, and keeps its two disks.

locals {
  docker_vms = local.netbox_guests.docker_vms

  # Built by hand and imported (imports.tf): never given an image or cloud-init.
  docker_vms_hand_built = ["107"]
}

# Debian 13's cloud image, a dated build pinned by its published checksum, on
# the shared NFS storage so any host can import it.
resource "proxmox_download_file" "debian_13_cloud" {
  node_name           = local.guest[keys(local.docker_vms)[0]].node
  datastore_id        = "syn-nfs"
  content_type        = "import"
  url                 = "https://cloud.debian.org/images/cloud/trixie/20261015-2632/debian-13-genericcloud-amd64-20261015-2632.qcow2"
  checksum            = "7f1d06b629fa01241ec5817735777d0554554ff8c8d9dc27f9eac65ba3c3a6f859deb6079ebc9c65795d1955abd00a77aa3ff9cb84ae6307dfbbd8b2e398dab2"
  checksum_algorithm  = "sha512"
  overwrite           = false
  overwrite_unmanaged = true

  lifecycle {
    ignore_changes = [node_name]
  }
}

# Cloud-init's vendor data for a new Docker VM: the QEMU agent, installed and
# started, so the provider's wait for it ends. The login and addresses are
# Proxmox's own cloud-init settings (initialization below).
resource "proxmox_virtual_environment_file" "docker_vm_vendor_data" {
  node_name    = local.guest[keys(local.docker_vms)[0]].node
  datastore_id = "syn-nfs"
  content_type = "snippets"

  source_raw {
    file_name = "docker-vm-vendor-data.yaml"
    data      = <<-EOT
      #cloud-config
      package_update: true
      packages:
        - qemu-guest-agent
      runcmd:
        - systemctl enable --now qemu-guest-agent
    EOT
  }

  lifecycle {
    ignore_changes = [node_name]
  }
}

moved {
  from = proxmox_virtual_environment_vm.docker_vm
  to   = proxmox_virtual_environment_vm.docker_vm["107"]
}

resource "proxmox_virtual_environment_vm" "docker_vm" {
  for_each = local.docker_vms

  node_name = local.guest[each.key].node
  vm_id     = tonumber(each.key)
  name      = local.guest[each.key].name
  on_boot   = true
  tags      = each.value.tags

  reboot_after_update = false

  bios          = "ovmf"
  machine       = "q35"
  scsi_hardware = "virtio-scsi-pci"
  tablet_device = false

  agent {
    enabled = true
  }

  # Two sockets when the vCPUs split evenly, as the first was built.
  cpu {
    type    = "x86-64-v2-AES"
    sockets = each.value.cores % 2 == 0 ? 2 : 1
    cores   = each.value.cores % 2 == 0 ? each.value.cores / 2 : each.value.cores
  }

  memory {
    dedicated = each.value.memory
    floating  = each.value.memory
  }

  efi_disk {
    datastore_id = "syn-nfs"
    file_format  = "raw"
  }

  disk {
    interface    = "scsi0"
    datastore_id = "syn-nfs"
    file_format  = "raw"
    import_from  = contains(local.docker_vms_hand_built, each.key) ? null : proxmox_download_file.debian_13_cloud.id
    size         = each.value.disk_gb
    discard      = "on"
    ssd          = true
  }

  dynamic "disk" {
    for_each = each.value.data_gb > 0 ? [each.value.data_gb] : []
    content {
      interface    = "scsi1"
      datastore_id = "syn-nfs"
      file_format  = "raw"
      size         = disk.value
      discard      = "on"
      ssd          = true
    }
  }

  dynamic "network_device" {
    for_each = each.value.bridges
    content {
      bridge      = network_device.value
      mac_address = try(local.guest[each.key].macs[network_device.key], null)
    }
  }

  dynamic "initialization" {
    for_each = contains(local.docker_vms_hand_built, each.key) ? [] : [each.key]
    content {
      datastore_id        = "syn-nfs"
      interface           = "ide2"
      vendor_data_file_id = proxmox_virtual_environment_file.docker_vm_vendor_data.id

      user_account {
        username = var.site.vm_login.user
        keys     = var.site.vm_login.ssh_keys
      }

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
  }

  operating_system {
    type = "l26"
  }

  serial_device {}

  lifecycle {
    prevent_destroy = true
    # Built once: a newer image or changed cloud-init is for the next VM, not a
    # reason to rebuild this one. HA or a migration moves it before the sync
    # records the new host.
    ignore_changes = [description, node_name, initialization, disk[0].import_from]

    precondition {
      condition     = contains(local.docker_vms_hand_built, each.key) || var.site.vm_login != null
      error_message = "A new Docker VM needs site.vm_login (the user cloud-init makes, and its SSH keys)."
    }
  }
}
