# Each guest's shape from NetBox, into git by a pull request

**The ask (2026-10-16):** edit a guest's shape in NetBox (what `tofu/proxmox/` holds in git:
cores, memory, disks, role, bridges, tags) and bring it into git with something run by hand,
which opens a pull request the way Renovate's updates do. `QUEUE-netbox.md`, Phase 6.

## Settled (the owner, 2026-10-16)

- **Scope: the plain containers, the Talos VMs, the Docker VMs and the Pi-holes**
  (`containers` and `piholes` in `guests.tf`, `talos_nodes` in `talos-config.tf`, `docker_vms`
  in `docker-vms.tf`; the Docker VMs added 2026-10-16, the owner: "a single source of truth,
  with corrections made if something strays"). What every VM of a kind shares (firmware, CPU
  type, datastore, a Pi-hole's swap and features) stays in OpenTofu; what varies is NetBox's. A
  NIC's bridge is its description's ("Proxmox bridge vmbrX"), so a bridge without an address
  works. The Pi-holes have no HA storage to record: neither is an HA resource or replicated,
  and both sit on `vms`, each node's own ZFS pool (Run Command task 874, 2026-10-19).
- **A Semaphore template that opens a pull request**, never a push to `main`: it writes the
  generated file, and when that differs from `main`, force-pushes the branch `netbox/guests`
  and opens a pull request, or updates the one already open, as Renovate does. Merging it is
  the owner's review; an apply is still run by hand from Semaphore.
- **NetBox owns the shape of those guests; git holds a generated copy.** One owner per value:
  the hand-written `talos_nodes` and `containers` maps go, replaced by the generated file.

## Rules

- **Only the shape enters git, never a name or an address.** The generated file holds VMIDs,
  roles, cores, memory, disk sizes, bridges, tags and apply modes: what `tofu/README.md` calls
  the shape. Names, hosts and addresses stay NetBox's, read at plan time (`netbox.tf`).
- **Where each field lives in NetBox:**
  | git | NetBox |
  |---|---|
  | role (`controlplane`, `worker`; or a plain container) | the VM's role: "Talos control plane", "Talos worker", "Plain container" |
  | cores, memory | the VM's vCPUs, memory (MB) |
  | `disk_gb`, `data_gb` (Talos); `disk_gb` (container) | virtual disks `scsi0`, `scsi1` (Talos); `rootfs` (container), in GB |
  | `apply_mode` (Talos) | custom field `talos_apply_mode` (`auto`, `no_reboot`) |
  | `tags` (container) | custom field `proxmox_tags` |
  | role "Pi-hole" | as a plain container: vCPUs, memory, `rootfs`, bridges, `proxmox_tags` |
  | role "Docker VM" | the VM's role "Docker VM": cores (vCPUs, two sockets when even), memory, disks `scsi0` and `scsi1`, bridges, `proxmox_tags` |
  | `bridges` (container, Docker VM) | each interface's description ("Proxmox bridge vmbrX"), else its address's prefix to the bridge `netbox_seed.yaml`'s networks name |
- **The nightly sync never overwrites what NetBox owns.** For a VM with one of the three roles,
  `scripts/netbox_seed.py` leaves vCPUs, memory, disks and those custom fields alone and
  reports where Proxmox differs; it still records the host, interfaces, MACs and status.
  Otherwise a resize made in NetBox would be put back from Proxmox before its pull request
  merged.
- **A failed read is not an empty list.** The generator stops, opening nothing, when NetBox
  doesn't answer, answers with no Talos node, or a VM with a role lacks a field; a guest
  removed from NetBox shows in the pull request as removed, and `prevent_destroy` still stops
  an apply destroying a Talos VM.
- **Deterministic output:** sorted keys, a fixed layout, so a run with nothing changed opens
  nothing and a real change is a small diff.

## Where things live

- **Input:** NetBox (roles, VM fields, virtual disks, custom fields), read by GraphQL with a
  read-only token (`NETBOX_TOKEN`, already forwarded to every Semaphore task).
- **Output:** `tofu/proxmox/netbox-guests.json`, read by `jsondecode(file(...))` in the
  module's locals; the branch `netbox/guests` and its pull request.
- **The tool:** `scripts/netbox_guests.py` (generate; `--pr` to push the branch and open or
  update the pull request), its tests in `scripts/tests/`. The template "NetBox: Guests to
  git" (Python), with a GitHub token (`GITHUB_TOKEN`, a secret environment variable).

## Checklist

- [x] NetBox: the three VM roles (`vm_roles` in `netbox_seed.yaml`), the custom fields
      `talos_apply_mode` (choice set "Talos apply modes") and `proxmox_tags`, and each guest's
      virtual disks from Proxmox (MB, a Proxmox G as 1000): sync task 736, 2026-10-16
- [x] Today's shapes entered in NetBox: the 7 Talos VMs took their role and apply mode from a
      one-time table in the seed (task 736), since removed; the generated file equals the
      hand-written `talos_nodes` it replaced
- [x] The seed leaves the owned fields alone for VMs with those roles, reporting differences
      (`differ` in `netbox_seed.py`): sync task 738 found nothing to change and none differing
- [x] `scripts/netbox_guests.py` with tests (`scripts/tests/test_netbox_guests.py`)
- [x] `tofu/proxmox/` reads `netbox-guests.json`; the hand-written maps out; a plan unchanged
      (task 737, "No changes")
- [x] `--pr` through GitHub's API: the branch moved to main, the file committed, the pull request
      opened or updated; the token only in a header (tested against a fake GitHub)
- [x] The owner's GitHub token, in Semaphore's "Netbox Sync" group as `GITHUB_TOKEN`
- [x] The template "NetBox: Guests to git" (template 24, 2026-10-16: a Python app,
      `scripts/netbox_guests.py`, its CLI argument `--pr` typed plainly, which Semaphore stores as
      `["--pr"]`; the "Netbox Sync" group). Task 752: no change, no pull request; task 753, VM 115's
      memory raised in NetBox: pull request #15, one line in `netbox-guests.json`
- [x] Proved end to end: #15 merged 2026-10-16; plan 762 showed VM 115's memory 10240 -> 11264
      (beside the Docker VM commit's adds), apply 763 wrote it, and plan 770 had nothing left
      for 115
- [ ] VM 115 (worker w05) running on its 11264 MB: Proxmox holds it, the node still has 10240
      (up since 2026-10-15) until its next drained reboot, the owner's
- [x] The Pi-holes (2026-10-19): the role "Pi-hole" in NetBox (`vm_roles`), on VMIDs 100 and 109
      with `proxmox_tags`; `local.piholes` in `guests.tf` replaced by the generated `piholes`,
      whose values equal the hand-written ones
- [ ] After that pull request merges: a plan (template 14, `params.plan`) with no change for
      `proxmox_virtual_environment_container.pihole`; then template 24 run once, finding
      nothing to change

## Resuming

The first unticked box is next. A half-done pass is safe: until the module reads the generated
file, git's maps rule, and the generator only writes a file and a branch.

## Done when

A change to a guest's shape is made in NetBox, arrives as a pull request from the Semaphore
template, and an apply makes Proxmox match (shown with VM 115's memory, once that node has
rebooted), and the Pi-holes' shape is NetBox's with a plan unchanged; then this file moves to
`old-queues/`.

## Checked and negative

- None yet.
