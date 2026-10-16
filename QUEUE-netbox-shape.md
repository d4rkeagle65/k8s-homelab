# Each guest's shape from NetBox, into git by a pull request

**The ask (2026-10-16):** edit a guest's shape in NetBox (what `tofu/proxmox/` holds in git:
cores, memory, disks, role, bridges, tags) and bring it into git with something run by hand,
which opens a pull request the way Renovate's updates do. `QUEUE-netbox.md`, Phase 6.

## Settled (the owner, 2026-10-16)

- **Scope: the plain containers and the Talos VMs** (`containers` in `guests.tf`,
  `talos_nodes` in `talos-config.tf`). The Pi-holes and the Docker VM keep hand-written shapes:
  theirs carry settings NetBox has no field for (HA storage, five fixed bridges).
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
  | `bridges` (container) | each interface's address, by its prefix, to the bridge `netbox_seed.yaml`'s networks name |
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

- [ ] NetBox: the three VM roles, the custom fields `talos_apply_mode` and `proxmox_tags`, and
      the seed writing each guest's virtual disks from Proxmox
- [ ] Today's shapes entered in NetBox (each Talos VM's role, sizes, disks, apply mode) and
      checked equal to `talos_nodes`
- [ ] The seed leaves the owned fields alone for VMs with those roles, reporting differences
- [ ] `scripts/netbox_guests.py` with tests: the file generated from NetBox equals today's maps
- [ ] `tofu/proxmox/` reads the file; the hand-written maps out; a plan unchanged
- [ ] `--pr`: the branch, the pull request opened or updated by GitHub's API, the token never
      printed (`scripts/CLAUDE.md`'s rule on arguments, and none in an error)
- [ ] The owner makes a fine-grained GitHub token for this repository only (Contents and Pull
      requests: read and write) and the template
- [ ] Proved end to end: a resize in NetBox, the template's pull request, merged, a plan
      showing just that change

## Resuming

The first unticked box is next. A half-done pass is safe: until the module reads the generated
file, git's maps rule, and the generator only writes a file and a branch.

## Done when

A change to a Talos VM's or a plain container's shape is made in NetBox, arrives as a pull
request from the Semaphore template, and an apply makes Proxmox match; then this file moves to
`old-queues/`.

## Checked and negative

- None yet.
