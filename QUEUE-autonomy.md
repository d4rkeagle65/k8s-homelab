# Getting more done: what I do on my own, and what waits on the owner

**The ask (2026-10-16):** the work has been slow, mostly waiting on round trips (a task ID, a
"push it", a deletion). Streamline it: list what I can do on my own given a go-ahead, what's
waiting on the owner, and the permissions that let me carry a piece of work through. Kept
here so a later session, after a compaction, knows where things stand.

## Settled (the owner, 2026-10-16)

- **Approval by named batch, delivered as pull requests** (2026-10-18): an approved batch below
  is one I carry through without asking first, but each piece of it ends as its own branch and
  pull request once the commit hook's suite passes, and the owner merges it; only a commit to
  the queue files alone goes straight to `main` (`CLAUDE.md`). Anything outside the named
  work, or a change of plan inside it, waits for the owner's go-ahead.
- **Deletions:** I delete Released PVs and retired database clusters myself, only after proving
  each one's data exists elsewhere (a healthy live copy, its data checked), and report each with
  its evidence. A prune-protected object goes by hand only once git no longer lists it.
- **Semaphore:** once `claude-code` is a Task Runner, I start dry runs, OpenTofu plans (then
  reject them), read-only "Tool: Run Command" checks and the NetBox sync myself. A dry run
  goes in the task's `"params": {"dry_run": true}`: a top-level `dry_run` is ignored and the
  task runs for real. I never confirm an OpenTofu apply or run a template that changes hosts
  unless asked for that run.
- **No physical Proxmox host is rebooted without the owner's approval** (2026-10-16), even inside an
  approved batch: the reboot template, an upgrade that reboots, or a command.
- **Decisions:** Kea's standby partner is (b), the reservations in a CloudNativePG cluster
  (`QUEUE.md`, Kea phase 3); VLAN 9 comes out of Proxmox (`old-queues/QUEUE-netbox.md`); Immich's
  PostgreSQL move isn't due (`QUEUE-updates.md`); `guests` is out of `TF_VAR_site`.

## Where the batches went

Every batch the owner approved on 2026-10-16 (zammad-bridge, released-volumes,
monitoring-fixes, talos-cleanup, resource-limits, alerts, hardening, small-cleanups,
cluster-policy, netbox-shape, ssh-keys, kea-ha, postgres-retire, netbox-dns, elasticsearch)
is done, recorded in git history and `old-queues/`, or carried on in its own queue file:
resource-limits in `QUEUE-resource-limits.md`, kea-ha in `QUEUE-kea-ha.md`. The owner's items
left are held where their work is: the node-loss timing in `QUEUE.md` ("Faster recovery from
a node loss"), `nas-block` and the power-cut failover test in `QUEUE-talos.md`, the
TerraMaster SNMP report in `QUEUE.md`.

## Resuming

Read this file for the standing permissions above, then `QUEUE.md`. An approved piece of work
is named in the conversation or in a commit message; without one, ask before starting it.

## Done when

Every batch is done or moved to its own queue file, and the owner's list is empty (both since
2026-10-22). What's left is the standing permissions under "Settled", which are rules, not
work: this file moves to `old-queues/` once they live in `CLAUDE.md` (the owner's call).

## Checked and negative

- None yet.
