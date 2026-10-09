# Working queue: OpenTofu for what's created through APIs

## The ask

Describe in code what's created through APIs (Proxmox guests first, then Cloudflare, Dockhand
and maybe the *arr apps), so it can be rebuilt and reviewed like the cluster is. OpenTofu
creates the thing; Ansible configures what runs inside it (`QUEUE.md`, "Ansible for the hosts
outside the cluster").

## Decided

- **OpenTofu, run from Semaphore.** Semaphore's image ships OpenTofu (1.11.0 in v2.19.12) and
  runs plans and applies as task templates, like the playbooks. Applies run by hand; a
  scheduled plan may report drift.
- **State in Semaphore's PostgreSQL**, database `tfstate`, through OpenTofu's `pg` backend:
  `tfstate-db-init` in `docker/semaphore/compose.yaml` keeps the role and database. The state
  holds secrets and describes the nodes the cluster runs on, so it lives neither in git nor in
  the cluster. Each root module gets its own schema (`schema_name`), one state per target.
  Backing it up is the `QUEUE.md` item for the Docker VM's databases.
- **Layout:** `tofu/<target>/`, one root module per target (`tofu/proxmox/` first), with
  `tofu/README.md` for the Semaphore setup. Versions pinned exactly: OpenTofu to Semaphore's,
  each provider to one release, and `.terraform.lock.hcl` committed. The claude-code image
  carries the same OpenTofu for `tofu fmt` and `tofu validate` (`init -backend=false`); it
  never reaches the state.
- **No real values in git:** host names, node names, network prefixes, last octets and domains
  come from one JSON variable per root module (`TF_VAR_site`), a secret in Semaphore whose
  master copy is a Vaultwarden note. Git holds the shape: VMIDs, roles, sizes, bridges, tags.
  Container MACs aren't stated at all (computed, kept as Proxmox assigned them); the Docker
  VM's are, since its network devices are one list attribute.
- **Import, never recreate:** every existing guest comes in through `import` blocks. The first
  plan must show only imports and no changes; any change it shows is a mismatch in the code,
  fixed in the code.
- **Read-only until the code matches:** the first token is read-only (PVEAuditor), so a plan
  can't change anything even by mistake. A token that can change guests comes only once the
  plan is clean, with only the privileges the resources need.

### Proxmox (`tofu/proxmox/`)

- **Provider `bpg/proxmox`** (v0.116.0 when written), the maintained one. Resources
  `proxmox_virtual_environment_container` and `proxmox_virtual_environment_vm`; endpoint and
  token from `PROXMOX_VE_ENDPOINT` and `PROXMOX_VE_API_TOKEN` in Semaphore's environment.
- **In scope:** the six k8s node LXCs (101-106), both Pi-holes' LXCs (100, 109) and the Docker VM (107).
  **Out of scope:** the Windows DC (108), left alone.
- **What an API token can't set stays with Ansible:** the raw `lxc.*` lines on the k8s nodes
  (apparmor unconfined, cap.drop, cgroup devices, proc/sys rw) and Pi-hole (`/dev/net/tun`),
  and the `mount=nfs` feature (the provider: changing features other than nesting needs
  `root@pam`). The resources list them under `lifecycle.ignore_changes` and the Proxmox host
  playbook owns them. A new node is created by OpenTofu, finished by that playbook, and joined
  by the node join playbook.
- **Every guest has `prevent_destroy`**; Pi-hole's Proxmox `protection` flag stays on.
- **Guest settings read 2026-10-07 (read-only API):** k8s nodes privileged, `nesting=1,mount=nfs`,
  onboot, swap 0, 8G rootfs on `vms`, `mp0` (container storage) 32G on control planes and 48G
  on workers, workers' `mp1` 128G at `/opt/local-path-provisioner`, 2 cores, 4 GiB (control)
  or 8 GiB (workers), eth0 on vmbr7 with the gateway and eth1 on vmbr0 without one. Pi-hole:
  unprivileged, `nesting=1,keyctl=1`, five interfaces (vmbr7/0/2/6/8), 2 GiB. Docker VM: q35,
  OVMF with an EFI disk, 2 sockets x 2 cores, 8 GiB, balloon off, scsi0 32G and scsi1 64G
  (discard, ssd), three NICs (vmbr0/7/8), guest agent on.

## Plan

1. **State and tools:** the `tfstate` database (Semaphore stack redeploy) and OpenTofu in the
   claude-code image (rebuild).
2. **`tofu/proxmox/` written and validated** here: backend, provider, variables, the guests,
   import blocks.
3. **Semaphore:** a read-only Proxmox token, the environment (`PG_CONN_STR`,
   `PROXMOX_VE_*`, `TF_VAR_site`), and a plan template. Plan until it shows imports only.
4. **Import:** a token that can change guests, one apply that imports, then a plan that shows
   no changes.
5. **Next targets**, each its own root module: Cloudflare (DNS records, the tunnel's public
   hostnames), then Dockhand (`kalebharrison/dockhand`, unofficial), then decide between the
   devopsarr providers and an in-cluster Job for the *arr settings.

## Checklist

- [ ] `TFSTATE_DB_PASSWORD` set in Dockhand (marked secret, copy in Vaultwarden); Semaphore
      stack redeployed; `tfstate-db-init`'s log ends `tfstate database owned by tfstate`
- [x] claude-code image rebuilt; `tofu version` says 1.11.0
- [x] `tofu/proxmox/` written; `tofu fmt -check` and `tofu validate` pass; lock file committed
- [x] The containers plan as imports only (claude-code, read-only token, local state): 8 to
      import, and 8 "to change" that are only `vm_id` and `timeout_start` entering state on
      the first apply (the provider sends nothing to Proxmox for those)
- [x] `TF_VAR_site` in Semaphore and Vaultwarden (built from the live config)
- [x] Proxmox token for OpenTofu, Semaphore variable group and template (`tofu/README.md`)
- [x] Plan from Semaphore (2026-10-08): 9 to import, 0 to add, 0 to destroy; the Docker VM
      imports with no differences, the containers' 8 changes are state-only
- [x] Import applied (2026-10-08): all 9 guests in state; the next plan says "No changes".
      The VM's update needed `VM.Config.Options` after all (its name is sent with every
      update), now in the `TofuDockerVM` role
- [x] A trusted certificate on each Proxmox host (2026-10-09, `ansible/proxmox-host.yml`:
      Let's Encrypt via Proxmox's ACME and Cloudflare DNS, Pi-hole records written by the run)
- [ ] The endpoint set to a host's name and `PROXMOX_VE_INSECURE` removed in Semaphore, a plan
      showing no changes
- [ ] A token that can change guests, once the certificate is in place
- [ ] Cloudflare root module

## Resuming

Read this file, then `git log --oneline -- QUEUE-terraform.md tofu docker/semaphore`.

## Done when

Every in-scope Proxmox guest and Cloudflare record is in OpenTofu state, a plan from Semaphore
shows no changes, and a new guest can be added by code.
