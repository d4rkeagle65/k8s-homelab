# Homelab work queue

Remaining work on the cluster and on this repo, most important first.
Delete an item when it's done; the commit that deletes it is the record.
Work finished up to 2026-10-04 is in the Done section of
`git show ed9e8a8:QUEUE.md`.

How the repo is managed:

- **Helm releases**: captured from the cluster by `capture`; `generate`
  writes their Flux files.
- **`.handwritten` releases** (media, traefik, traefik-isolated,
  external-secrets, smtp-relay, local-path-provisioner): the tool never
  touches them.
- **`.promote` releases**: capture writes the namespace's non-Helm objects
  into `app/`, and Secrets go to git as `${PLACEHOLDER}`s.
- **Secret values**: fields of the `cluster-secrets` item in Vaultwarden,
  synced into the cluster by External Secrets (`kubernetes/secrets/`). List
  or change them with `scripts/vaultwarden-fields.ps1`. Plain settings are in
  `kubernetes/flux/meta/vars/cluster-settings.yaml`.

Run everything from the repo root with `python scripts/backup.py all`.

Database backups are handled outside this repo and the cluster config, so
nothing here tracks them.

## Next

Nothing right now; pick from Later.

## Cleanup

- [ ] **`test/media/jackett`** stays as a test-only app for now; not listed in
  `test/kustomization.yaml`.

## Later

- [ ] **DNS: drop the second DNS server DHCP hands out** (or make it a second
  Pi-hole). It doesn't know local names, so a PC that asks it caches "no such
  host" for names like the cluster API endpoint. That causes intermittent
  `kubectl` lookup failures until the cache clears.
- [ ] **immich's Redis eviction policy.** immich's job queue (BullMQ) logs that
  Redis uses `volatile-lru` and should use `noeviction`; under memory
  pressure, queued jobs could be dropped. Check which Redis immich uses
  before changing it, since a shared Redis affects its other users too.
- [ ] **Keep immich and manictime up through a node loss** (optional). Today
  their single database copy sits on one worker's disk.
  - Set `instances: 2` on `immich-postgres` and `manictime-pg`, for automatic
    failover in under a minute.
  - Shorten the app pods' `node.kubernetes.io/unreachable`/`not-ready`
    tolerations from the default 5 minutes to about 30 seconds.
  - Cost: double the database disk on another worker.
- [ ] **Proxmox host cleanup.**
  - Run `apt autoremove` on each host to clear the Proxmox 8 leftovers.
  - Once kernel 7.0 has run cleanly for a couple of weeks, remove
    `6.14.11-4` (keep `6.14.11-9` as the fallback): `/boot` is only 456 MB.
  - Don't run `zpool upgrade` unless a new ZFS feature is needed.

- [ ] **SSH to the k8s nodes only from the management network.** Each node
  now has a management-network interface (`eth1`), so sshd can listen there
  only, or a firewall can limit port 22 to it. Keep Calico on `eth0` (see
  CLAUDE.md).
- [ ] **MetalLB: announce only on `eth0`.** The L2Advertisement
  (`cluster/l2advertisement.metallb.io/`) has no `interfaces` list, so since
  the nodes got `eth1` MetalLB may also answer for service addresses on the
  management network. Add `interfaces: [eth0]`; check first how `generate`
  treats the file (change the live object and recapture, or hand-edit).
- [ ] **Kea phase 3:** a second Kea on another host as a hot-standby partner
  (the firewall's relay can point at both), and lease names into DNS.
- [ ] **One Proxmox host carries both Pi-hole and the Docker VM**, so its
  reboot takes DNS, DHCP, the VPN and Vaultwarden down
  together. A second Pi-hole and a Kea HA partner on other hosts would fix
  that.
- [ ] **The Vaultwarden secret stores didn't recover by themselves** after
  Vaultwarden's move on 2026-10-03, despite `refreshInterval: 1m`: they
  stayed `InvalidProviderConfig` until the recheck annotation
  (`kubectl annotate clustersecretstore <name> homelab.local/revalidate=<time> --overwrite`).
  Find out whether External Secrets rechecks a store that's already invalid,
  or only valid ones, and make recovery automatic.
- [ ] **Ansible for the hosts outside the cluster**, so their hand-made setup
  can be rebuilt from git instead of from notes:
  - **Docker VM:** netplan (its three network legs, routing tables and rule
    priorities), the nft scripts and their systemd units (management return
    path, DMZ marks, the `DOCKER-USER` rules for Tailscale), sysctls, Docker,
    Dockhand's own compose file and the certificate-sync cron.
  - **Proxmox hosts:** repositories, packages, kernel cleanup, and the host
    settings the k8s LXCs depend on.
  - **Pi-hole:** its `pihole-FTL --config` settings (the `local=` lines,
    HTTPS filtering, no conditional forwarding, `bogusPriv`) and the
    hand-made local records such as `vaultwarden-direct`.

  The inventory and every address stay out of this public repo (a
  gitignored inventory, or values from Vaultwarden).
- [ ] **Terraform (or OpenTofu) for what's created through APIs:**
  - **Proxmox:** the VMs and LXCs (k8s nodes, the Docker VM, Pi-hole), with
    their disks and network interfaces.
  - **Cloudflare:** DNS records and the tunnel's public hostnames.
  - **The *arr settings:** download clients, root folders, auth and
    Prowlarr's app links, through the devopsarr providers. This is the
    alternative to a small in-cluster Job calling the apps' APIs, which
    needs no state file.
  - **Dockhand:** its Git credentials, the Git stacks it deploys from
    `docker/<stack>/` and their variables, plus its own settings and login,
    through the community `kalebharrison/dockhand` provider (unofficial,
    with known gaps, so a Dockhand update can break it). Dockhand can't set
    itself up: Ansible installs it on the VM, then Terraform configures it.

  Before starting: decide where the state file lives (it holds secrets,
  including the stacks' secret variables, so not this repo) and what runs it
  (by hand, CI, or tofu-controller under Flux). Import the existing resources
  first, so nothing gets recreated.
