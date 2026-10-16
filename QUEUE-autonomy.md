# Getting more done: what I do on my own, and what waits on the owner

**The ask (2026-10-16):** the work has been slow, mostly waiting on round trips (a task ID, a
"push it", a deletion). Streamline it: list what I can do on my own given a go-ahead, what's
waiting on the owner, and the permissions that let me carry a piece of work through. Kept
here so a later session, after a compaction, knows where things stand.

## Settled (the owner, 2026-10-16)

- **Pushes by named batch:** the owner may approve a named piece of work below as "push when
  the tests pass". Every commit within it then goes to `main` once the commit hook's suite
  passes. Anything outside the named work, or a change of plan inside it, waits for "push it".
  Each push says which approval it falls under; `main` is pushed by a command of its own.
- **Deletions:** I delete Released PVs and retired database clusters myself, only after proving
  each one's data exists elsewhere (a healthy live copy, its data checked), and report each with
  its evidence. A prune-protected object goes by hand only once git no longer lists it.
- **Semaphore:** once `claude-code` is a Task Runner, I start dry runs, OpenTofu plans (then
  reject them), read-only "Tool: Run Command" checks and the NetBox sync myself. I never
  confirm an OpenTofu apply or run a template that changes hosts unless asked for that run.
- **Decisions:** Kea's standby partner is (b), the reservations in a CloudNativePG cluster
  (`QUEUE.md`, Kea phase 3); VLAN 9 comes out of Proxmox (`QUEUE-netbox.md`); Immich's
  PostgreSQL move isn't due (`QUEUE-postgres18.md`); `guests` is out of `TF_VAR_site`.

## Batches I can do on my own

Each is a name the owner can approve with "push when the tests pass". Where a batch needs a
Semaphore run, it needs the Task Runner role, or the owner runs it.

- [ ] **postgres-retire** (from 2026-10-17): the old `<app>-postgres` clusters (ten in
      `QUEUE-postgres18.md`, ManicTime's and Whisparr's in `QUEUE-updates.md`) out of git, each
      app checked a day on 18; then the Clusters and their claims deleted, each new cluster's
      data checked first
- [ ] **released-volumes:** the 38 Released PVs (7 of the old `netbox` namespace, 29 `local-db`
      and 4 `nas-block` of the destroyed LXC nodes, 1 `nfs-retain-rwo`), each checked against
      its live replacement, then deleted; the NFS one's folder on the NAS left to the owner
- [ ] **talos-cleanup:** `talos/gen-config.py` reading the cluster's settings from
      `TF_VAR_site` instead of the `kubeadm-config` ConfigMap, then the ConfigMap deleted; the
      destroyed nodes out of local-path's maps; the `nas-block` storage class and its tasks
      (the LUNs need a Semaphore run of `proxmox-host.yml`)
- [ ] **resource-limits:** batches 5 (monitoring) and 6 (Calico, MetalLB, Flux), the
      LimitRanges and PriorityClasses, and the 2026-10-18 re-measure
      (`QUEUE-resource-limits.md`)
- [ ] **monitoring-fixes:** Pi-hole 1's slow exporter; the three Proxmox hosts' node-exporters
      down since 2026-10-15 (`QUEUE.md`; a Run Command check, then `host-exporters.yml`);
      Whisparr's exportarr; etcd scraping (one control plane at a time)
- [ ] **alerts:** monitoring's phase 5: memory near its limit, CPU throttling, a container
      restarting in a loop, drive wear and spare capacity, the TerraMaster's SNMP target down
- [ ] **hardening:** health probes for the containers still without (`QUEUE.md`), Pod Security
      labels namespace by namespace (each checked with a server-side dry run), shorter
      node-loss tolerations for the apps that matter
- [ ] **small-cleanups:** Lidarr's `migrations/` folder; Garage's config off the inline
      compose file; the Docker live-restore playbook (decision 7, agreed), off until set
- [ ] **netbox-shape:** `QUEUE-netbox-shape.md`, all but the GitHub token
- [ ] **netbox-dns:** `QUEUE-netbox.md` phase 4, NetBox owning DNS (large)
- [ ] **ssh-keys:** a playbook removing the four deleted Semaphore keys from `authorized_keys`,
      keeping `semaphore-shared`, checked on every host first; VLAN 9 out of Proxmox
- [ ] **kea-ha:** Kea's reservations onto CloudNativePG, then the standby partner (decision 5)

## Waiting on the owner

- [x] `claude-code` made a Task Runner in Semaphore (2026-10-16, checked by `/api/project/1/role`)
- [x] Decision 10, the test folders: both kept, the split explained in each `CLAUDE.md` (2026-10-16)
- [ ] A fine-grained GitHub token for this repository (Contents and Pull requests, read and
      write) for `QUEUE-netbox-shape.md`'s template
- [ ] A limited Dockhand user for Homepage's widget (`QUEUE.md`)
- [ ] Delete task 713 in Semaphore (it holds two replaced tokens)
- [ ] Delete `coprlxctest01a` in NetBox
- [ ] Organizr's browser checks (`QUEUE-organizr.md`)
- [ ] In person: the failover test (a host's power cut, `QUEUE-talos.md`), VT-x in the third
      host's BIOS, the second Pi-hole on the DMZ's static clients, the TerraMaster SNMP bug
      reported

## Resuming

Read this file first, then `QUEUE.md`. An approved batch is named in the conversation or in a
commit message; without one, work stops at the branch. The first unticked batch the owner has
approved is next.

## Done when

Every batch is done or moved to its own queue file, and the owner's list is empty; then this
file moves to `old-queues/`.

## Checked and negative

- None yet.
