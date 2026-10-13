# Working in tofu/

`README.md` here explains the modules, their site values and their Semaphore templates.

- **Changing a running container reboots it, with no drain and no opt-out.** The Proxmox
  provider reboots a container after updating its memory, CPU, disks, mount points or
  network, and OpenTofu updates independent resources in parallel: one apply that touches
  every worker reboots every worker at once. Change one Kubernetes node per apply (a commit
  per node, or `-target`; a Talos node is added to `talos_nodes` by a commit of its own),
  drain it first (`kubectl drain --ignore-daemonsets --delete-emptydir-data`), and wait for it
  to be Ready before the next. The Docker VM and the Talos VMs have
  `reboot_after_update = false`, so their reboots are done by hand.
