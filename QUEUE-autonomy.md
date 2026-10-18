# Getting more done: what I do on my own, and what waits on the owner

**The ask (2026-10-16):** the work has been slow, mostly waiting on round trips (a task ID, a
"push it", a deletion). Streamline it: list what I can do on my own given a go-ahead, what's
waiting on the owner, and the permissions that let me carry a piece of work through. Kept
here so a later session, after a compaction, knows where things stand.

## Settled (the owner, 2026-10-16)

- **Approval by named batch, delivered as pull requests** (2026-10-18): an approved batch below
  is one I carry through without asking first, but each piece of it ends as its own branch and
  pull request once the commit hook's suite passes, and the owner merges it. Nothing reaches
  `main` any other way (`CLAUDE.md`). Anything outside the named work, or a change of plan
  inside it, waits for the owner's go-ahead.
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
  (`QUEUE.md`, Kea phase 3); VLAN 9 comes out of Proxmox (`QUEUE-netbox.md`); Immich's
  PostgreSQL move isn't due (`QUEUE-postgres18.md`); `guests` is out of `TF_VAR_site`.

## Approved (the owner, 2026-10-16)

**Every batch below, and `zammad-bridge` and `elasticsearch`, is approved.** Work through them
in this order, a pull request for each piece once the commit hook's suite passes, reporting
as I go, and stopping only for a real decision
or a step that needs the owner in person or in a UI: zammad-bridge, released-volumes,
monitoring-fixes, talos-cleanup, resource-limits, alerts, hardening, small-cleanups,
netbox-shape, ssh-keys, kea-ha, postgres-retire (from 2026-10-17), netbox-dns, elasticsearch.

- [x] **zammad-bridge:** Alertmanager to Zammad's Checkmk endpoint (the owner enabled it, group
      "Alerts"; its URL is `ZAMMAD_ALERT_WEBHOOK_URL` in Vaultwarden): severities `warning` and
      `critical` only (never `Watchdog`, `InfoInhibitor` or `info`), a ticket per alert, a note on
      repeats, closed with a note on recovery. A small translator in the cluster, with tests
- [x] **elasticsearch** (done 2026-10-16, after cluster-policy's LimitRange OOM-killed its init): the ECK operator 3.5.0 and the chart's one node
      (512 MiB heap in 1 GiB, the chart's sizes rather than the 1 GiB heap first planned, which
      Zammad's handful of tickets doesn't need; 5 GiB on `nas-iscsi`); checked in
      `QUEUE-zammad.md`

## Batches I can do on my own

Each is a name the owner can approve, to be carried through to its pull requests. Where a
batch needs a Semaphore run, it needs the Task Runner role, or the owner runs it.

- [ ] **postgres-retire** (from 2026-10-17): the old `<app>-postgres` clusters (ten in
      `QUEUE-postgres18.md`, ManicTime's and Whisparr's in `QUEUE-updates.md`) out of git, each
      app checked a day on 18; then the Clusters and their claims deleted, each new cluster's
      data checked first
- [x] **released-volumes** (2026-10-16): the owner deleted the 38 checked PVs. Left on disk,
      unreferenced: the old NetBox media folder on the NAS and its database files on four Talos
      workers' local-db disks
- [x] **talos-cleanup** (2026-10-16): `gen-config.py` reads `TF_VAR_site`, `kubeadm-config`
      deleted, the LXC workers out of local-path, `nas-block`'s class gone. Left with the
      owner: `nas-block` off the hosts and the NAS (`QUEUE-talos.md`)
- [ ] **resource-limits:** batches 5 (monitoring) and 6 (Calico, MetalLB, Flux), the
      LimitRanges and PriorityClasses, and the 2026-10-18 re-measure
      (`QUEUE-resource-limits.md`)
- [ ] **monitoring-fixes:** done 2026-10-16: the Proxmox hosts' node-exporters (a boot race,
      fixed in `host-exporters.yml` and run), Pi-hole 1's hung exporter (restarted, probed by
      `/metrics`). Whisparr's exportarr was already up. Left, needing the owner: etcd scraping, a change to every
      control plane's Talos config, rolled node by node with `talosctl patch mc --mode=no-reboot`
      (tofu/CLAUDE.md), then an OpenTofu apply the owner confirms
- [x] **alerts** (2026-10-16): `monitors/app/prometheusrule-homelab.yaml`: a container near
      its memory limit, a Docker VM container restarting in a loop, NVMe wear (80% warning, 90%
      critical), spare capacity and critical-warning flags, the TerraMaster's SNMP target down.
      Each ran against live data first; only H3's NVMe (86%) fires, as known
- [x] **hardening** (2026-10-16): Pod Security's `baseline` enforced on 31 namespaces (Zammad's
      privileged init container gone first); probes for n8n's runners and External Secrets'
      controller. Left with the owner: node-loss timing (a Talos control-plane change, `QUEUE.md`),
      as for etcd scraping; `restricted` for 13 namespaces once their Job templates are checked
- [x] **small-cleanups** (2026-10-16): Lidarr's `migrations/` out; Garage's config written into
      a volume at each deploy (checked on the NAS); the Docker live-restore and upgrade
      switches in `docker-vm-update.yml` (off until the owner sets them); and found on the way,
      Run Command's `run_command_b64` for commands holding braces
- [x] **cluster-policy** (2026-10-16): `kubernetes/prod/system/cluster-policy/cluster-policy/`
      holds the PriorityClass `homelab-critical` and a `container-defaults` LimitRange per apps
      and services namespace (`test_every_app_and_service_namespace_has_its_limitrange` keeps
      the list whole). The class is on the 14 PostgreSQL 18 clusters (CloudNativePG applies it
      at each pod's next recreation, without a roll), Authentik's server and worker, Traefik,
      Prometheus, Alertmanager and zammad-bridge. Authentik's outposts don't carry it: a class
      there goes in the outpost's `config`, owned in Authentik's UI, and a blueprint setting it
      would own all twenty keys
- [x] **netbox-shape** (2026-10-16): built and proved up to the template, which is the
      owner's (`QUEUE-netbox-shape.md`, its last two boxes)
- [ ] **netbox-dns:** `QUEUE-netbox.md` phase 4, NetBox owning DNS (large)
- [x] **ssh-keys** (2026-10-16): `ansible/retire-ssh-keys.yml` and `proxmox-host.yml`'s
      retired-interfaces play, built and checked; their runs are the owner's (below)
- [ ] **kea-ha:** `QUEUE-kea-ha.md`: `kea-db` up and proved (2026-10-16); next the copy, then
      the owner's cutover; the second Docker VM waits on its host and NetBox entry

## Waiting on the owner

- [x] `claude-code` made a Task Runner in Semaphore (2026-10-16, checked by `/api/project/1/role`)
- [x] Decision 10, the test folders: both kept, the split explained in each `CLAUDE.md` (2026-10-16)
- [x] A fine-grained GitHub token for this repository, in Semaphore's "Netbox Sync" group
- [ ] Pull request #15 (VM 115's memory, from template 24's task 753): merge it for the
      end-to-end plan, or close it and put NetBox back.
      The test container (VMID 199) stays as Decommissioning, the generator's skip case once it has the role
      "Plain container"
- [ ] A limited Dockhand user for Homepage's widget (`QUEUE.md`)
- [ ] Delete task 713 in Semaphore (it holds two replaced tokens)
- [ ] Set `docker_live_restore: true` on "Node: Docker Update" and run it; later
      `upgrade_docker: true` (`QUEUE.md`, decision 7)
- [ ] Two Talos control-plane changes, rolled node by node with `talosctl` and then an
      OpenTofu apply you confirm: etcd's metrics, and a 60-second node-loss timing
- [ ] `nas-block` off the hosts and the NAS, together (`QUEUE-talos.md`)
- [x] Where the PriorityClass and LimitRanges live (2026-10-16): a hand-written release,
      `kubernetes/prod/system/cluster-policy/`, owning both (batch **cluster-policy** below)
- [ ] Your real run of "Retire: Old SSH Keys" (its dry run, task 745, was clean). VLAN 9 is out
      of Proxmox (task 748); delete its `vmbr9` and `eth5.9` interfaces in NetBox
- [x] Kea's three variables in Vaultwarden (2026-10-16)
- [ ] netbox-dns: the sidecar is deployed and works (it was asked for 37 records and skipped
      each: NetBox DNS has no zone). Create the zone(s) in NetBox DNS (the domain the Ingresses
      use, with its SOA nameserver); the records follow within a minute
- [x] Dockhand's environment for the second Docker VM, connected (2026-10-17: the 429 was
      Hawser blocking a token it didn't match; Standard mode takes the agent's own token)
- [ ] `NETBOX_READ_TOKEN` in Vaultwarden without its "Bearer " prefix (its users accept either),
      so Prometheus can discover the Docker VMs from NetBox's Prometheus SD plugin (it adds its
      own "Bearer "); then node-exporter's targets come from the `docker_vm` tag
- [ ] The Pi-holes' routing (`QUEUE.md`, found 2026-10-16): they answer every network from
      Production
- [ ] Organizr's browser checks (`QUEUE-organizr.md`)
- [x] Vaultwarden's `GRIMMORY_ADMIN_EMAIL` set to the local admin's `+grimmory` address
      (2026-10-16, through the bitwarden-cli pod; checked in Grimmory's sync Secret)
- [ ] In person: the failover test (a host's power cut, `QUEUE-talos.md`), the second Pi-hole on the DMZ's static clients, the TerraMaster SNMP bug
      reported

## Resuming

**Where the run stands (2026-10-16, late, the owner away):** done and pushed today beyond the
batches above: Docker VMs from NetBox (VM 120 built, Docker and Hawser on it), kea-db with the
reservations copied (the cutover and partner wait for the owner), Zammad's search, the Ansible
task counter, the baseline's management routing, the NetBox DNS sidecar (waiting on zones), the
Talos queue's hostnames gone and the hook's word list extended. Next in order: kea-ha's partner
(with the owner: Dockhand's environment first), postgres-retire (from 2026-10-17), the owner's
list below. Each piece: its own branch and pull request, opened once the commit hook's suite
passed; the owner merges. Semaphore: dry runs and plans in `params`; the owner runs anything
that installs or changes hosts; never a Proxmox host reboot unasked.

Read this file first, then `QUEUE.md`. An approved batch is named in the conversation or in a
commit message; without one, ask before starting it. The first unticked batch the owner has
approved is next.

## Done when

Every batch is done or moved to its own queue file, and the owner's list is empty; then this
file moves to `old-queues/`.

## Checked and negative

- None yet.
