# OpenTofu

What's created through APIs, described in code: one root module per target, each with its own
state. Plans and applies run from Semaphore (`docker/semaphore/`), whose image ships the same
OpenTofu as `versions.tf` requires. The decisions it was built on are in `old-queues/QUEUE-terraform.md`; what's left is in `QUEUE.md`.

| Module | Owns |
|---|---|
| `proxmox/` | The Proxmox guests: the Talos VMs (`talos.tf`, with their machine configs in `talos-config.tf`), both Pi-holes, the Docker VMs (`docker-vms.tf`: a new one from Debian's cloud image, its login from `site.vm_login`) and plain Debian containers (`containers` in `guests.tf`); every guest's shape from `netbox-guests.json` (not the Windows DC). |
| `cloudflare/` | The hand-made DNS records in both Cloudflare zones (not the tunnel controller's records, the tunnel's public hostnames, or cert-manager's challenges: each has its own owner). |

## What lives where

- **The shape** (VMIDs, roles, sizes, bridges, tags) is in git, and nothing else: no names,
  addresses or numbers from the site. The Talos VMs', plain containers', Docker VMs' and Pi-holes' shape is NetBox's
  (old-queues/QUEUE-netbox-shape.md): `proxmox/netbox-guests.json` is generated from it by
  `scripts/netbox_guests.py`, which Semaphore's "NetBox: Guests to git" runs to open a pull
  request; never edit the file by hand.
- **Each guest's Proxmox host, name, addresses and MACs are NetBox's** (`netbox.tf`): one
  GraphQL query at every plan, by the VMID custom field, with a read-only token. NetBox's
  nightly sync (`scripts/netbox_seed.py`) keeps them in step with Proxmox. A plan stops when
  NetBox doesn't answer, and warns when a guest lacks a node, name or main-network address.
  The Docker VM and the Talos VMs ignore their host (`node_name`): HA or a migration moves
  them before the sync records it.
- **A new guest starts in NetBox:** a virtual machine there with status Planned, the VMID
  in its `vmid` custom field, its Proxmox host as its device (the one with the most free
  memory), its name, and an interface `net0` with its address. A Talos VM or plain container
  also gets its shape there: role, vCPUs, memory, virtual disks (`scsi0`, `scsi1`; `rootfs`, in
  MB, a G as 1000) and `talos_apply_mode` or `proxmox_tags`. Its pull request merged, an apply
  builds it; the nightly sync marks it active and adds its MACs, and never writes the shape
  back over NetBox's (it reports where Proxmox differs).
- **A guest being removed is Decommissioning in NetBox:** the nightly sync writes nothing
  about it, and the generator keeps its entry exactly as `main`'s `netbox-guests.json` has
  it, so a plan neither changes nor destroys it. Deleting it in NetBox drops its entry.
- **What's specific to the site and NetBox doesn't hold** is one JSON value, `TF_VAR_site`:
  each bridge's network prefix and gateway, the nodes' DNS settings, and the Talos cluster's
  values. `variables.tf` describes it and checks it before anything is read.
  - Its **master copy is a Vaultwarden note** ("OpenTofu site (Proxmox)"). Semaphore hides a
    secret once saved, so a change is made in the note and the whole note pasted into the
    `TF_VAR_site` secret, then checked with a plan.
- **The state** is in Semaphore's PostgreSQL, database `tfstate`, schema per module
  (`backend "pg"`); it holds the same site values, so it never goes in git.
- **What an API token can't set** (container features other than nesting) is set at
  creation and ignored afterwards; Ansible owns it.

## Semaphore setup (once per module)

1. **A Proxmox API token for OpenTofu** (Datacenter > Permissions): a user `tofu@pve`, a token
   `semaphore` with privilege separation off, and the user given `PVEAuditor` on `/`. Reading
   the Docker VM's disks needs `VM.Config.Disk`, and any update to it needs
   `VM.Config.Options` (the provider sends the VM's name with every update). To change and
   create guests (the Talos VMs, QUEUE-talos.md), the user has a role `TofuGuests` on `/`
   beside `PVEAuditor`, with only what the guests need: `VM.Allocate`, `VM.Audit`,
   `VM.Config.*` (CDROM, CPU, Cloudinit, Disk, HWType, Memory, Network, Options),
   `VM.PowerMgmt`, `VM.Migrate`, `VM.GuestAgent.Audit`, `Datastore.AllocateSpace`,
   `Datastore.AllocateTemplate`, `Datastore.Audit`, `SDN.Use`, `Sys.Audit` and
   `Sys.AccessNetwork` (downloading an image by URL). No backups, snapshots, consoles or host
   settings. A new Talos version replaces `proxmox_download_file.talos_nocloud`, and deleting
   the old image needs `Datastore.Allocate`, which also allows deleting any volume on that
   storage: so a role `TofuImages` with only that, on `/storage/syn-nfs` alone, where the
   images are. On a Proxmox host:

   ```sh
   pveum role add TofuGuests -privs VM.Allocate,VM.Audit,VM.Config.CDROM,VM.Config.CPU,VM.Config.Cloudinit,VM.Config.Disk,VM.Config.HWType,VM.Config.Memory,VM.Config.Network,VM.Config.Options,VM.PowerMgmt,VM.Migrate,VM.GuestAgent.Audit,Datastore.AllocateSpace,Datastore.AllocateTemplate,Datastore.Audit,SDN.Use,Sys.Audit,Sys.AccessNetwork
   pveum acl modify / -users tofu@pve -roles PVEAuditor,TofuGuests
   pveum role add TofuImages -privs Datastore.Allocate
   pveum acl modify /storage/syn-nfs -users tofu@pve -roles PVEAuditor,TofuGuests,TofuImages
   ```

   A path with its own entry replaces what a user inherits there, not adds to it: so the
   `/storage/syn-nfs` entry lists all three roles, and no
   entry on `/vms/...` for this user (an older `TofuDockerVM` entry on `/vms/107` is removed:
   `pveum acl delete /vms/107 -users tofu@pve -roles PVEAuditor,TofuDockerVM`, then
   `pveum role delete TofuDockerVM`).
2. **A variable group** (Semaphore's sidebar; "environment" in its API) "OpenTofu: Proxmox",
   as environment variables, not extra variables:
   - variable: `PROXMOX_VE_ENDPOINT` = `https://<a Proxmox host's name>:8006/`, the name its
     Let's Encrypt certificate carries (`ansible/proxmox-host.yml`). No
     `PROXMOX_VE_INSECURE`: the certificate is verified like any other;
   - secrets: `PROXMOX_VE_API_TOKEN` = `tofu@pve!semaphore=<secret>`,
     `PG_CONN_STR` = `postgres://tfstate:<TFSTATE_DB_PASSWORD>@semaphore-postgres:5432/tfstate?sslmode=disable`,
     `TF_VAR_site` = the JSON value, `TF_VAR_talos_secrets` = the Talos secrets (`TALOS_SECRETS`
     in Vaultwarden: the base64 of a `secrets.yaml`), `TF_VAR_netbox_token` = a read-only
     NetBox token (Vaultwarden's `NETBOX_READ_TOKEN`); variable `TF_VAR_netbox_url` =
     `https://netbox.<domain>`. The state then holds the Talos secrets and
     an admin certificate for every Talos node's API, so it's as secret as they are.
3. **A template**, type OpenTofu, repository this one, subdirectory `tofu/proxmox`, that
   environment. Each run plans first and waits for a confirmation before it applies; reject it
   to plan only.

## The Cloudflare module (`cloudflare/`)

- **What's specific to the site** is one JSON value, `TF_VAR_dns`: each zone's name and id,
  and the records keyed `r01`, `r02`, ... (meaningless on purpose, so git names nothing), each
  with its zone, Cloudflare's record id, and the record. Its **master copy is a Vaultwarden
  note** ("OpenTofu DNS (Cloudflare)"), handled like the Proxmox site note. A new record takes
  the next free key and an import block if it already exists in Cloudflare.
- **Token:** its own Cloudflare API token, Zone > DNS > Edit and Zone > Zone > Read on the two
  zones only (not cert-manager's or the tunnel controller's).
- **Semaphore:** a variable group "OpenTofu: Cloudflare", environment variables, all secrets:
  `CLOUDFLARE_API_TOKEN`, `PG_CONN_STR` (the same as Proxmox's; the schema differs) and
  `TF_VAR_dns` (the note). A template, type OpenTofu, subdirectory `tofu/cloudflare`.
- **A record that already exists in Cloudflare** is adopted with an import block, keyed by
  its `r` key, its id from `TF_VAR_dns`, removed again once applied: OpenTofu takes import keys
  only written out, not looped over a variable (`import { to = cloudflare_dns_record.this["r30"]
  id = "${var.dns.zones[var.dns.records["r30"].zone]}/${var.dns.records["r30"].id}" }`). A new
  record needs none: the apply creates it.

## Checks for a change here

- `tofu fmt -check -recursive` and `tofu validate` (after `tofu init -backend=false`) pass.
- A plan from Semaphore shows only what the change meant to change. Every guest has
  `prevent_destroy`, so a change that would replace one fails the plan instead.
