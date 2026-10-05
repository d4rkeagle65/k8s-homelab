# Homelab work queue

Outstanding work on the cluster and on this repo, roughly in the order worth doing it.
`CLAUDE.md` holds the rules for keeping it. Database backups are handled outside this repo, so
nothing here tracks them.

## Contents

- [Next](#next)
- [Cleanup](#cleanup)
- [Later](#later)
- [Working queue files](#working-queue-files) - none open.

## Next

Nothing right now; pick from Later.

## Cleanup

- **`test/media/jackett`** stays as a test-only app for now; not listed in
  `test/kustomization.yaml`.

## Later

- **Keep immich and manictime up through a node loss** (optional). Today
  their single database copy sits on one worker's disk.
  - Set `instances: 2` on `immich-postgres` and `manictime-pg`, for automatic
    failover in under a minute.
  - Shorten the app pods' `node.kubernetes.io/unreachable`/`not-ready`
    tolerations from the default 5 minutes to about 30 seconds.
  - Cost: double the database disk on another worker.
- **Proxmox host cleanup.**
  - Run `apt autoremove` on each host to clear the Proxmox 8 leftovers.
  - Once kernel 7.0 has run cleanly for a couple of weeks, remove
    `6.14.11-4` (keep `6.14.11-9` as the fallback): `/boot` is only 456 MB.
  - Don't run `zpool upgrade` unless a new ZFS feature is needed.

- **SSH to the k8s nodes only from the management network.** Each node
  now has a management-network interface (`eth1`), so sshd can listen there
  only, or a firewall can limit port 22 to it. Keep Calico on `eth0` (see
  CLAUDE.md).
- **Kea phase 3:** a second Kea on another host as a hot-standby partner
  (the firewall's relay can point at both), and lease names into DNS.
- **One Proxmox host carries both Pi-hole and the Docker VM**, so its
  reboot takes DNS, DHCP, the VPN and Vaultwarden down
  together. A second Pi-hole and a Kea HA partner on other hosts would fix
  that.
- **Ansible for the hosts outside the cluster**, so their hand-made setup
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
- **Terraform (or OpenTofu) for what's created through APIs:**
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

## Working queue files

None open. A long job gets a `QUEUE-<task>.md` at the repo root; `CLAUDE.md` says when. A
finished one moves to `old-queues/`, whose README indexes what each settled and which checks
came back negative.
