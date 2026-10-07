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
  4. the host carrying the Docker VM goes last, and the VM (Semaphore runs on it) is
     live-migrated to a host with room before the reboot and back afterwards: the inventory's
     `evacuate`, checked for memory and bridges first, the move read back after.
  5. the Pi-holes are found by their Proxmox tag (`dns_guest_tag`): a host carrying one
     reboots only while every other one answers a lookup, and the run waits for its own to
     answer again; a host carrying all of them (two or more) is left for a reboot by hand.
- **Upgrades run by hand.** A scheduled run, if any, only reports what's pending (check mode).

- **The k8s checks use a read-only ServiceAccount** (list nodes, `/readyz`, `/livez`) in a
  shared `cluster-access` namespace (`kubernetes/prod/system/cluster-access/semaphore/`), one
  folder per outside account. Semaphore calls the API with `ansible.builtin.uri` (no kubectl in
  its image), the CA pinned. claude-code's account moves there too, in one step: a Flux
  Kustomization's name must equal its folder and be unique, so `cluster-access/claude-code`
  can't sit beside `system/claude-code/claude-code`. The move recreates the ServiceAccount, so
  its token changes: rerun `setup-kubeconfig` with the new one (`docker/claude-code/README.md`),
  then delete the empty `claude-code` Namespace (Flux never prunes Namespaces).
- **capture leaves a cluster-wide object an app's own Kustomization applies alone**
  (`FluxOwnership.app_owned_reason`): the ClusterRole is in its folder already, and a copy
  under `kubernetes/cluster/` would have cluster-resources apply it too.

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
- [x] `AUTHENTIK_SEMAPHORE_OIDC_CLIENT_SECRET` in Vaultwarden and `cluster-secrets`
- [x] Stack deployed; OIDC login works; owner promoted to admin (the built-in admin needs an email of its own: Semaphore matches OIDC logins by email)
- [x] Playbook and its checks written (syntax-checked; the cluster checks run clean against the live API)
- [x] VM evacuation and the DNS rule (tested against the live Proxmox API and Pi-hole with stand-ins for `pvesh`, `qm` and `hostname`)
- [x] Semaphore set up (repo, inventory, key, known hosts, templates)
- [x] Check-mode run clean on all four hosts (2026-10-05; one host's extra `ceph-tentacle` source disabled to match the others)
- [ ] A real run that reboots hosts, the Docker VM moved off and back: waits for a kernel update, which the check template lists
- [ ] claude-code's account moved into `cluster-access` (moved in git; left: rerun `setup-kubeconfig`, delete the `claude-code` Namespace)

## Resuming

Read this file, then `git log --oneline -- QUEUE-semaphore.md docker/semaphore ansible`.

## Done when

The Proxmox hosts are upgraded from Semaphore, one at a time, with the checks stopping a run
before it touches a host while the clusters aren't healthy.

## Checked and negative

- **Semaphore's OIDC can't make users admins** (v2.19.12, `util/OdbcProvider.go`: no admin
  or group claim); promotion is a one-time step in its UI.
