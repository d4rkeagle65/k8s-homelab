# Homelab work queue

Outstanding work on the cluster and on this repo, roughly in the order worth doing it.
`CLAUDE.md` holds the rules for keeping it.

## Contents

- [Waiting on the owner](#waiting-on-the-owner)
- [Next](#next)
- [Cleanup](#cleanup)
- [Later](#later)
- [Working queue files](#working-queue-files) - one `QUEUE-<task>.md` per long job.

## Waiting on the owner

Decisions that unblock the work below; each gives a recommendation. Numbers stay as
assigned (items below refer to them); 1 to 6 and 8 to 10 are done, 7 waits on a setting.

- **Homepage's Dockhand login is Dockhand's admin** (`DOCKHAND_HOMEPAGE_USERNAME`/`_PASSWORD`,
  `services/homepage/`): Dockhand drives Docker through its socket, so its admin is root on the
  Docker host, and Homepage holds the password. Make a limited Dockhand user for the widget and
  put it in those two fields.
7. **Docker's live-restore, then Docker upgrades from Semaphore:** built 2026-10-16 in
   `ansible/docker-vm-update.yml`, both off until set (its header says what each does). The
   owner sets `docker_live_restore: true` on "Node: Docker Update" and runs it; then
   `upgrade_docker: true` once the live-restore run has read back on.

## Next

- **bitwarden-cli's `bw serve` exits when several forced syncs reach it at once** (found
  2026-10-18): four `vaultwarden-fields.ps1` listings run together, each POSTing
  `/sync?force=true` through `kubectl exec`, restarted the container twice (about three
  minutes without the CLI; `bitwarden-notes` went `InvalidProviderConfig` and came back on its
  own). Not memory: 142 of 512 MiB and no OOM event; the process exited with an error.
  External Secrets, the container's liveness probe (`/sync?force=true` every two minutes) and
  the script all go through it, so two forced syncs meeting can take the cluster's secrets
  down for minutes. Find the error in the previous container's log (grep for the error line
  only: the log carries its login), then serialize forced syncs or stop the probe and the
  script forcing one.
- **rreading-glasses keeps only some of a new author's works** (upstream issue 602, open;
  found 2026-10-18): its first load of an author gets every work from Hardcover, but attaches
  them in batches that each start from the pre-refresh snapshot (`getAuthor` serves the
  `ra<id>` key while a refresh runs, by design per the declined PR 509), so the last batch
  overwrites the others and Bookshelf shows a short bibliography (Cassandra Clare: 3 books;
  six of eleven authors lost most works). Repaired by hand for all eleven: once a refresh has
  finished, a work fetched on its own attaches to the live record, so the missing works' cache
  rows (`w<id>` in `rreading-glasses-db`'s `cache` table) were deleted, the pod restarted (it
  also caches in memory) and each work fetched (`GET /work/<id>`), then RefreshAuthor in
  Bookshelf. The full list per author comes from its refresh's `ensuring author-work edges`
  lines with `VERBOSE=true`. Left: an author added from now on can come out short the same
  way; a small job that does this repair (or a patched image, or the fix upstream) would end
  it. Already-repaired authors keep their works across refreshes (the snapshot is complete).
  **Pace any repair to Hardcover's daily budget** (2026-10-18: about 2,000 repair lookups
  in an afternoon spent the token's whole day, `rg_gql_ratelimit_remaining{bucket="daily"}`
  0, and every uncached Bookshelf lookup failed for hours): read that metric before and
  during a batch and stop well short of 0; one work every few seconds, not one a second
  (rreading-glasses already keeps to the per-second burst budget; its pacing is
  hard-coded); never a repair while Bookshelf is identifying new files, or several large
  authors at once. The HardcoverDailyBudget alerts (homelab.bookshelf) ticket both the
  low and the spent budget. Left from that day: Terry Brooks's last 82 works, and the 17
  Shannara files (renamed .prc to .mobi, in `Shannara/`) to map, once the budget resets.
- **systemd-resolved caches "no such name" on the Debian hosts** (found 2026-10-17): a hostname
  asked for before ExternalDNS made its record stayed unresolvable there though the Pi-holes
  answered it. `tasks/resolver-cache.yml` (in `baseline.yml`, 2026-10-17) sets
  `Cache=no-negative` where resolved runs; left: the owner's baseline run
- **The Pi-holes answer every network from Production** (found 2026-10-16, Run Command task
  784): from their management, DMZ and other addresses, replies leave by `eth0`, Production's
  default route, so a client on another subnet reaching one of those addresses through the
  firewall gets no answer (the asymmetric routing that hid the new Docker VM's SSH). They're
  containers whose networks Proxmox configures, so `tasks/management-routing.yml` only
  reports them: give each address its own table and rule (as the Docker VMs have), through
  the containers' network config, and check DMZ clients' DNS before and after.
- **Docker on the Docker VM was upgraded by "Node: Docker Update" (task 749, 2026-10-16), against
  its hold:** `docker-vm-update.yml` read the installed packages through the command module,
  which expanded `${Package}` as an environment variable, so its list to hold was empty and it
  upgraded docker-ce to 29.9.1, restarting the daemon and every container on the VM (Kea,
  Vaultwarden, Semaphore, NetBox, Tailscale; all back within seconds; Semaphore lost the task,
  which shows "running" forever). Fixed: the read goes through the shell, and the run stops
  unless every installed Docker package reads back held. Live-restore is still off: the run's
  `docker_live_restore` was a survey variable targeting "Environment", which Ansible never
  sees. Left: the survey's target set to "Variable", a dry run, then the run; task 749 stopped
  in Semaphore by the owner.
- **A LimitRange's defaults reach a Job's pods only when they run** (found 2026-10-16): the
  check before `cluster-policy` read the pods present, so Zammad's init job, gone by then, met
  the 512 MiB default only at its next upgrade and was OOM-killed. Before a change to the
  defaults, read each Job and CronJob template in the namespaces (and their past peaks in
  Prometheus), not only the running pods.
- **tigera-operator lands back on a node that's shutting down** (found 2026-10-16): its
  tolerations are "every NoSchedule and NoExecute taint" (the chart's default), so when a
  Talos node shuts down each replacement pod is scheduled onto it again and rejected; 92
  rejected pods piled up when a worker went down (cleared by hand). Narrow `tolerations` in
  `prod/system/tigera-operator/tigera-operator/app/values.yaml` to the control-plane,
  `not-ready` and `network-unavailable` taints (it must still start before the CNI is ready);
  left as is, every node shutdown leaves a pile and the operator is down until it ends.
- **Containers still without health probes** (2026-10-16): NetBox's worker on the Docker VM
  (no port: an exec probe on the rq worker, in `docker/netbox/compose.yaml`). n8n's task
  runners (the launcher's `/healthz` on 5680) and External Secrets' controller (its chart's
  probes) got theirs; cert-manager's cainjector has no health endpoint (its 1.21 chart offers
  no probe); the config-reloader and dashboard sidecars, Calico, kube-proxy, Authentik's
  outposts and the tunnel connector belong to their charts and operators.
- **Pod Security: `restricted` where it holds.** Since 2026-10-16 every namespace whose pods
  pass enforces `baseline` (`pod-security.kubernetes.io/enforce` in its `namespace.yaml`; each
  checked by a server-side dry run of the label and its workload templates, CronJobs
  included). Left unlabelled, and so `privileged` (the control planes' cluster-wide
  default): calico-system, tigera-operator, monitoring, metallb-system, metrics-server,
  democratic-csi, homeassistant (host network, host paths, NET_RAW) and local-path-storage
  (its provisioner's helper pods mount host paths). Next: `restricted` for the 13 that pass it
  today (cert-manager, cluster-access, cnpg-system, external-dns, external-secrets,
  external-services, nebula-sync, redis-ha, renovate, smtp-relay, both Traefiks), once their
  CronJob and Job templates are checked against it too.
- **Prometheus doesn't scrape etcd** (`kube-prometheus-stack`'s `kubeEtcd` is off), so there's
  no WAL fsync latency, leader changes or database size history: on 2026-10-13 the Talos control
  plane's commit latency (26-31 ms against 9-25 ms) had to be sampled by hand. The control
  planes are all Talos since 2026-10-13, so etcd's metrics endpoint is set in one place, the
  Talos config (`talos/patches/`): add it there, then turn `kubeEtcd` on.
- **The second Proxmox host's NVMe drive (512 GB, the one carrying its guests) is at 86% of its
  rated wear** (`nvme_percentage_used_ratio`, 2026-10-10: 62 TB written over 4.1 years). It's
  written at about 108 GB a day now (a week of `nvme_data_units_written_total`, 2026-10-13),
  with a Kubernetes worker on it: its last 14% (about 10 TB) lasts about three months at that
  rate. Replace it, a ZFS disk swap, or move the worker off it sooner. The first host's 256 GB boot drive is at 66% (about 1.4 years). Every host's
  drives write 40 to 70 GB a day on lifetime average, far more than Prometheus did: once a few
  days of `node_disk_written_bytes_total` and `pve_disk_written_bytes` exist, find the writers
  (etcd, the workers' databases, images and logs, Proxmox's own files) and cut what can be.
  Alerts on wear and spare capacity are phase 5 of `QUEUE-monitoring.md`.

- **Kubernetes 1.37, when its patches settle:** `kubernetes_version` in
  `tofu/proxmox/talos-config.tf` in a commit, then `ansible/talos-k8s-upgrade.yml` (its dry run
  first), then the OpenTofu job. Check first that Talos 1.14 supports 1.37 (its support
  matrix) and the add-ons' ranges: Calico 3.33 is the one that covers 1.37 (3.33.0 tested on
  1.35-1.37). Bump the claude-code image's `KUBECTL_VERSION` once the cluster is on it.

## Cleanup

- **`test/media/jackett`** stays as a test-only app for now; not listed in
  `test/kustomization.yaml`.

## Later

- **Lidarr back to stable tags** (from 2026-10-18 it runs the nightly channel, pinned, for
  qBittorrent's API key; Renovate's `linuxserver/lidarr` rule follows nightlies only): once a
  stable release is past the nightly running, set `tag` in `prod/apps/media/lidarr/app/
  helmrelease.yaml` to it and remove that rule. Left alone, Lidarr stays on nightly builds.
- **Faster recovery from a node loss** (with the owner: a Talos control-plane change): every database now fails over in under a minute, but
  the app pods on a lost node wait out the default 5-minute
  `node.kubernetes.io/unreachable`/`not-ready` tolerations before moving. One place sets that
  for every pod: the API server's `--default-not-ready-toleration-seconds` and
  `--default-unreachable-toleration-seconds` (the DefaultTolerationSeconds admission plugin),
  in the Talos control planes' config: about 60 seconds, rolled node by node with `talosctl
  patch mc --mode=no-reboot` (tofu/CLAUDE.md), then the OpenTofu apply the owner confirms.
- **Proxmox host cleanup.**
  - Once kernel 7.0 has run cleanly for a couple of weeks (from about 2026-10-16), set
    `remove_old_kernels: true` on the Proxmox inventory and run the upgrade template: with the
    default `keep_kernels: 2` it removes `6.14.11-4` and keeps `6.14.11-9` as the fallback.
    Every run already lists what it would remove.
  - Don't run `zpool upgrade` unless a new ZFS feature is needed.

- **Pi-hole 1's sshd still doesn't come back after a container restart**, despite the
  hand-made drop-in (`ssh.service.d/20-after-network-online.conf`, 2026-10-10) meant to make
  sshd, which listens on the management address only, wait for the network: after a restart
  around 12:30 UTC on 2026-10-15 it refused connections until restarted by hand on 10-16,
  and every Semaphore run on Pi-hole 1 failed meanwhile. Find out why on the next restart
  (`journalctl -b -u ssh`; is the drop-in on both Pi-holes, and is
  `systemd-networkd-wait-online` or its equivalent enabled in the container?), then have
  `baseline.yml` own the fix on every host whose sshd sets `ListenAddress`.
- **Kea phase 3** (`QUEUE-kea-ha.md`; decision 5, the owner 2026-10-16: (b)): Kea's reservations move from
  PostgreSQL on the Docker VM to a CloudNativePG cluster reached through a LoadBalancer
  address, so a partner can read them while the Docker VM is down; then a second Kea on the
  second Pi-hole's host as a hot-standby partner (the firewall's relay can point at both), and
  lease names into DNS.
- **DMZ clients don't know about the second Pi-hole**: Kea doesn't serve the DMZ, so its
  static clients still list only `172.16.228.3` and lose DNS while Pi-hole 1's host
  reboots. Add `172.16.228.4` on each by hand.
- **The Proxmox inventory's `evacuate` can point back at the host with the most free memory:**
  VT-x is on in every host (Run Command task 755, 2026-10-16: `vmx`, `kvm_intel` and `/dev/kvm`
  on all five), so the override that steered the Docker VM's evacuation away from the third host
  is no longer needed. Check the config context in NetBox, then drop it.
- **The Docker VM's host still carries DNS, DHCP, the VPN and Vaultwarden together**: a Kea
  HA partner on another host (decision 5) is what's left once both Pi-holes are in use.
- **Dockhand's own data** (`/opt/dockhand`, SQLite: its environments, Git stacks and their
  variables, secrets included) has no backup of its own beyond the Proxmox backup of the Docker
  VM's disks. The databases and Vaultwarden there are backed up to Garage nightly
  (`docker/databasus/`, `vaultwarden-backup` in `docker/vaultwarden/`).
- **Ansible for the hosts outside the cluster**, so their hand-made setup
  can be rebuilt from git instead of from notes:
  - **Docker VM:** netplan (its three network legs, routing tables and rule
    priorities), the nft scripts and their systemd units (management return
    path, DMZ marks, the `DOCKER-USER` rules for Tailscale), sysctls, Docker,
    Dockhand's own compose file and the certificate-sync cron.
  - **Proxmox hosts:** `ansible/proxmox-host.yml` holds the ZFS ARC cap, the ACME
    certificates and the NAS block storage (all applied), and `baseline.yml` the common
    packages. Still to add there: repositories and kernel cleanup.
  - **Pi-hole:** `ansible/pihole-config.yml` sets what nebula-sync can't copy
    (`misc.dnsmasq_lines`) on both, from Semaphore. Everything else (local
    records such as `vaultwarden-direct`, CNAMEs, lists, `bogusPriv`) lives on
    Pi-hole 1 and is copied to Pi-hole 2 by nebula-sync, so losing both would
    lose it: a scheduled Teleporter export to Garage would cover that.
  - **Named logins instead of root:** the owner's account with sudo is on every machine
    (`baseline.yml`, run 2026-10-10). Once it's proven everywhere: remove the owner's
    key from root's `authorized_keys` (on the Proxmox hosts the shared
    `/etc/pve/priv/authorized_keys`), and set `sshd` to `PasswordAuthentication no` and
    `PermitRootLogin prohibit-password` (never `no`: the Proxmox hosts and Semaphore log in
    as root by key), from a playbook that first checks the named login works.
  - **Scheduled check-mode runs in Semaphore** (weekly, failure alerts on),
    reporting pending updates and drift without changing anything.

  The inventory and every address stay out of this public repo (a
  gitignored inventory, or values from Vaultwarden).
- **OpenTofu for the rest of what's created through APIs** (decision 9; the Proxmox guests and
  Cloudflare's DNS records are done, `tofu/`, `old-queues/QUEUE-terraform.md`):
  - **Cloudflare:** the tunnel's public hostnames.
  - **The *arr settings:** download clients, root folders, auth and
    Prowlarr's app links, through the devopsarr providers. This is the
    alternative to a small in-cluster Job calling the apps' APIs, which
    needs no state file.
  - **Dockhand:** its Git credentials, the Git stacks it deploys from
    `docker/<stack>/` and their variables, plus its own settings and login,
    through the community `kalebharrison/dockhand` provider (unofficial,
    with known gaps, so a Dockhand update can break it). Dockhand can't set
    itself up: Ansible installs it on the VM, then Terraform configures it.

## Working queue files

- [QUEUE-autonomy.md](QUEUE-autonomy.md): read first: the owner's standing permissions, the batches I can do on my own, and what waits on the owner.
- [QUEUE-monitoring.md](QUEUE-monitoring.md) (about 55%): Prometheus, Loki and Grafana, in eight phases.
- [QUEUE-resource-limits.md](QUEUE-resource-limits.md) (about 75%): requests and limits on every container, in batches.
- [QUEUE-talos.md](QUEUE-talos.md) (about 93%): the nodes onto Talos VMs, surviving the loss of any host.
- [QUEUE-netbox.md](QUEUE-netbox.md) (about 65%): NetBox on the Docker VM as the source of Semaphore's hosts and OpenTofu's guests, then owning DNS.
- [QUEUE-netbox-shape.md](QUEUE-netbox-shape.md) (about 80%): each guest's shape edited in NetBox, into git by a pull request.
- [QUEUE-kea-ha.md](QUEUE-kea-ha.md) (0%): Kea's reservations on CloudNativePG, then a hot-standby partner.
- [QUEUE-zammad.md](QUEUE-zammad.md) (about 25%): Zammad, the helpdesk, with alerts becoming tickets.
- [QUEUE-organizr.md](QUEUE-organizr.md) (about 90%): Organizr configured from git; two sign-in checks left.
- [QUEUE-postgres18.md](QUEUE-postgres18.md) (about 85%): every database onto PostgreSQL 18 on Debian trixie, a new cluster each.
- [QUEUE-updates.md](QUEUE-updates.md) (about 70%): version updates that change behaviour (Mealie, MetalLB,
  Calico, Authentik, app-template, PostgreSQL majors, Kubernetes), one at a time.

The percentages are estimates as of 2026-10-16, from each file's checklist weighed by the
size of what's left; update one when its file changes. A long job gets a `QUEUE-<task>.md` at
the repo root; `CLAUDE.md` says when. A
finished one moves to `old-queues/`, whose README indexes what each settled and which checks
came back negative.
