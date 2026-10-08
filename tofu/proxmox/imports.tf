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
