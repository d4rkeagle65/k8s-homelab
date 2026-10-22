# Overnight, 2026-10-22: what needs no owner input

**The ask (the owner, 2026-10-22 00:10 UTC):** "work on what you can out of the queues without
my input. Make another overnight queue for those things. And save any decisions for me and
continue what you can."

## Settled

- Each piece ends as its own branch and pull request (`CLAUDE.md`); queue-only commits go to
  `main`. Nothing is merged, applied, deployed or deleted; no Semaphore template that changes
  a host runs; no NetBox or Key Vault write. Read-only Run Commands and dry runs are fine.
- Each piece is worked in its own git worktree, never the shared `netbox` one: two agents in
  one worktree switched each other's branches on 2026-10-21.
- Before calling a PR open, its state is read (`gh pr view`).
- A question only the owner can answer goes under "Decisions for the owner" and the work
  moves on; it never blocks the rest.

## Checklist

| Piece | `QUEUE.md` entry | Result |
|---|---|---|
| A | `test-script-health`'s standard-library check only knows the platform it runs on | |
| B | `test-harness` reads red in a Claude desktop worktree | |
| C | `keyvault-secrets.ps1` shows Dockhand's secrets as `Synced` `no` | |
| D | Nothing owns the Pi-holes' timezone | |
| E | NetBox's change-log API fails on Topology Views' role icons | |
| F | `docker/claude-code/README.md`'s "Updating" | |
| G | kea-standby's API doesn't answer from the owner's PC (read-only diagnosis) | |
| H | NetBox has none of MetalLB's addresses (`netbox_seed.py` reads the LoadBalancers) | |
| I | Morning reads: the Proxmox backup (01:00 UTC), rreading-glasses repair dry run (07:15 UTC), Immich import dry run (08:30 UTC) | |

## Decisions for the owner

- **`TALOS_SECRETS` isn't where `talos/README.md` says** (Vaultwarden `cluster-secrets`): found
  elsewhere (Key Vault `TALOS-SECRETS`, an old file), or regenerate it from a control plane's
  machine config while Semaphore's talosconfig still works?
- **Rotate `grimmory-db`'s login password?** A `SHOW GRANTS` printed its hash into the
  2026-10-21 session's output (not git).
- **#258 (database placement rule) restarts every database once:** when to merge; then a
  go-ahead to move about eight replicas, one at a time.
- **Pod Security:** 14 namespaces enforce `restricted`, 21 `baseline` (live, 2026-10-21). Keep
  the 21 on `baseline` (drop the `QUEUE.md` entry), or raise them one app at a time?
- **#260 (Dockhand backup):** `DOCKHAND-BACKUP-ZIP-PASSWORD` in Key Vault, then the stack in
  Dockhand, and its ID for the webhook.
- **The old Grimmory dump folder on the Synology**: delete it.
- **The `evacuate` override in NetBox's Proxmox config context**: drop it (a NetBox write).
- **DMZ clients' second Pi-hole address**, by hand on each.
- **The Pi-holes' DMZ addresses in git history:** rewrite history, or leave them.
- **Scheduled weekly check-mode runs in Semaphore** need a template schedule (Manager role).

Pieces that end in a decision add it here.

## Resuming

Read the checklist; a piece with a PR number is done (check its state); one without is
redone from its `QUEUE.md` entry in a fresh worktree from `origin/main`. Each piece stands
alone.

## Done when

Every row has a result (a PR, a finding in `QUEUE.md`, or a decision above), and the morning
report has gone to the owner. Then this file moves to `old-queues/` with a README row, and the
decisions still open move into `QUEUE.md`.
