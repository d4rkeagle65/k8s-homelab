# Working in tofu/

`README.md` here explains the modules, their site values and their Semaphore templates.

- **Changing a running container reboots it, with no drain or opt-out**, and an apply updates
  resources in parallel: touching every worker reboots them all. One Kubernetes node per apply
  (a commit per node or `-target`; a Talos node joins `talos_nodes` by its own commit), drained
  first, Ready before the next. The Docker VM and the Talos VMs reboot by hand
  (`reboot_after_update = false`). A change to every Talos control plane's config restarts
  every API server at once (a 50-second outage, 2026-10-13): roll it first, one node at a time,
  with `talosctl -n <node> patch mc --mode=no-reboot`, so the apply finds it done.
