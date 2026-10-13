# Brings the guests made by hand into state, so nothing is recreated. Once
# imported these are no-ops; a guest added later in guests.tf is created
# instead, and needs no entry here.
import {
  for_each = local.k8s_nodes
  to       = proxmox_virtual_environment_container.k8s_node[each.key]
  id       = "${var.site.guests[each.key].node}/${each.key}"
}

import {
  for_each = local.piholes
  to       = proxmox_virtual_environment_container.pihole[each.key]
  id       = "${var.site.guests[each.key].node}/${each.key}"
}

import {
  to = proxmox_virtual_environment_vm.docker_vm
  id = "${var.site.guests["107"].node}/107"
}

# The first Talos control plane, built by hand (talos/README.md).
import {
  to = proxmox_virtual_environment_vm.talos["110"]
  id = "${var.site.guests["110"].node}/110"
}
