# Working queue: Semaphore for Ansible

## The ask

Run Ansible from Semaphore (a web UI for Ansible and OpenTofu) in Docker on the Docker host,
starting with keeping the Proxmox hosts up to date, so the rolling upgrade done by hand
becomes a playbook with checks.

## Decided

- **A Dockhand Git stack, `docker/semaphore/`**: Semaphore v2.19.12
  (`semaphoreui/semaphore`, Ansible 13.5 inside) and its own PostgreSQL 17, data in bind
  mounts under `/opt/semaphore`.
- **Its web UI is published on the host's management address only**, on port 3001 (Dockhand
  has 3000), and served at `semaphore.<domain>` through Traefik from `external-services`:
  private-only (`websecure` plus `private-networks`), never on the tunnel.
- **Login through Authentik OIDC**, from a blueprint in `authentik-blueprints` like Dockhand's
  and Stork's, open to `authentik Admins` only. Semaphore has no admin mapping for OIDC users:
  its built-in admin (set from the stack's variables) promotes the owner's account once, and
  stays as the fallback login when Authentik is down.
- **Playbooks live in this repo, under `ansible/`**; Semaphore pulls them from git. The
  inventory (host names and addresses), the SSH key and any secrets live only in Semaphore,
  encrypted with its access-key key, never in git.
- **Semaphore gets an SSH key of its own** for the Proxmox hosts, revocable on its own, and the
  hosts' keys are pinned through `SEMAPHORE_SSH_KNOWN_HOSTS_FILE`: the image turns host-key
  checking off for every host.
- **The first playbook is the rolling Proxmox upgrade**, one host at a time:
  1. before each host, check the Proxmox cluster has quorum and every k8s node is Ready, and
     stop if not;
  2. `apt dist-upgrade` (Proxmox's way, not `apt upgrade`), then `apt autoremove`;
  3. reboot only when a reboot is required, wait for the host, its k8s LXCs and their nodes
     to come back Ready;
  4. the host carrying the Docker VM and Pi-hole goes last and is never rebooted by the
     playbook: Semaphore runs on it. The run ends by saying a reboot
     is needed there.
- **Upgrades run by hand.** A scheduled run, if any, only reports what's pending (check mode).

## Open

- **The k8s checks need the cluster.** Either a read-only ServiceAccount for Semaphore
  (get nodes, `/readyz`), in git like `claude-code`'s, or `pct exec` into a control-plane
  LXC from the Proxmox host. The ServiceAccount is cleaner; decide in batch 2.

## Plan

1. **Stack and login:** `docker/semaphore/` (compose, README), the Authentik blueprint, its
   client-secret Secret and worker env, the Traefik route. The client secret is a new
   `cluster-secrets` variable, pushed first.
2. **Playbook:** `ansible/` with the upgrade playbook, its checks, and a README; the k8s
   check's credential.
3. **Semaphore setup and a first run:** the repository, inventory, key and template in
   Semaphore's UI; a check-mode run, then a real one on one host.

## Checklist

- [x] Stack, blueprint, route written (the blueprint dry-runs valid in Authentik)
- [ ] `AUTHENTIK_SEMAPHORE_OIDC_CLIENT_SECRET` in Vaultwarden and `cluster-secrets`
- [ ] Stack deployed; OIDC login works; owner promoted to admin
- [ ] Playbook and its checks written
- [ ] Semaphore set up (repo, inventory, key, known hosts, template)
- [ ] Check-mode run clean; one real host upgraded through Semaphore

## Resuming

Read this file, then `git log --oneline -- QUEUE-semaphore.md docker/semaphore ansible`.

## Done when

The Proxmox hosts are upgraded from Semaphore, one at a time, with the checks stopping a run
before it touches a host while the clusters aren't healthy.

## Checked and negative

- **Semaphore's OIDC can't make users admins** (v2.19.12, `util/OdbcProvider.go`: no admin
  or group claim); promotion is a one-time step in its UI.
