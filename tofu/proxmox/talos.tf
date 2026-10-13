# The Talos VMs (QUEUE-talos.md): one per local.talos_nodes entry
# (talos-config.tf), each booted from the Image Factory's nocloud image, given
# its address by cloud-init, and sent its machine config. One node per apply:
# a control plane joins etcd, and the kubeadm one on its host is retired
# after it (talos/README.md), never two at once.

# The image, once, on the NAS's shared storage, which every host reads; the
# Image Factory serves it as qcow2, which Proxmox imports as a disk.
resource "proxmox_download_file" "talos_nocloud" {
  node_name    = var.site.guests[keys(local.talos_nodes)[0]].node
  datastore_id = "syn-nfs"
  content_type = "import"
  url          = "https://factory.talos.dev/image/${local.talos_schematic}/${local.talos_version}/nocloud-amd64.qcow2"
  file_name    = "talos-${substr(local.talos_schematic, 0, 12)}-${local.talos_version}-nocloud-amd64.qcow2"
  overwrite    = false
}

resource "proxmox_virtual_environment_vm" "talos" {
  for_each = local.talos_nodes

  node_name = var.site.guests[each.key].node
  vm_id     = tonumber(each.key)
  name      = var.site.guests[each.key].hostname
  on_boot   = true
  tags      = ["k8s", "talos", each.value.role == "controlplane" ? "k8s-control" : "k8s-worker"]

  # A Talos node is changed by its machine config, not by rebooting its VM: a
  # change here that needs a restart only warns, and the restart is done by
  # hand, after draining (tofu/CLAUDE.md).
  reboot_after_update = false

  scsi_hardware = "virtio-scsi-single"
  boot_order    = ["scsi0"]

  agent {
    enabled = true
  }

  cpu {
    type  = "x86-64-v2-AES"
    cores = each.value.cores
  }

  # The balloon device on with its minimum at the full size, as for the Docker
  # VM (guests.tf): the guest's real use is reported, nothing is taken back.
  memory {
    dedicated = each.value.memory
    floating  = each.value.memory
  }

  # The system disk, imported from the image at creation only (the provider
  # ignores import_from afterwards, so a node built by hand is left alone).
  disk {
    interface    = "scsi0"
    datastore_id = "vms"
    import_from  = proxmox_download_file.talos_nocloud.id
    file_format  = "raw"
    size         = each.value.disk_gb
    discard      = "on"
    ssd          = true
    iothread     = true
  }

  network_device {
    bridge = "vmbr7"
  }

  # Its address on the main network, read by Talos's nocloud platform; the
  # VM's name is its hostname, and so its Kubernetes node name.
  initialization {
    datastore_id = "vms"
    interface    = "ide2"
    ip_config {
      ipv4 {
        address = "${local.talos_address[each.key]}/${local.talos_net.prefix_length}"
        gateway = local.talos_net.gateway
      }
    }
    dns {
      servers = var.site.node_dns_servers
    }
  }

  operating_system {
    type = "l26"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Sends each new node its config, once it's up in maintenance mode, and again
# whenever the config changes (Talos applies what it can without a reboot).
resource "talos_machine_configuration_apply" "node" {
  for_each = { for id, n in local.talos_nodes : id => n if n.apply_config }

  client_configuration        = local.talos_client_configuration
  machine_configuration_input = data.talos_machine_configuration.node[each.key].machine_configuration
  node                        = local.talos_address[each.key]
  endpoint                    = local.talos_address[each.key]

  timeouts = {
    create = "20m"
    update = "20m"
  }

  depends_on = [proxmox_virtual_environment_vm.talos]
}
