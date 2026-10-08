# OpenTofu

What's created through APIs, described in code: one root module per target, each with its own
state. Plans and applies run from Semaphore (`docker/semaphore/`), whose image ships the same
OpenTofu as `versions.tf` requires. The decisions and what's left are in `QUEUE-terraform.md`.

| Module | Owns |
|---|---|
| `proxmox/` | The Proxmox guests: the six Kubernetes node containers, both Pi-holes and the Docker VM (not the Windows DC). |

## What lives where

- **The shape** (VMIDs, roles, sizes, bridges, tags) is in git, and nothing else: no names,
  addresses or numbers from the site.
- **What's specific to the site** is one JSON value, `TF_VAR_site`: each bridge's network
  prefix and gateway, the nodes' DNS settings, and per guest its Proxmox host, host name and
  last octet (the Docker VM: its MACs instead). `variables.tf` describes it and checks it
  before anything is read.
  - Its **master copy is a Vaultwarden note** ("OpenTofu site (Proxmox)"). Semaphore hides a
    secret once saved, so a change is made in the note and the whole note pasted into the
    `TF_VAR_site` secret, then checked with a plan.
  - It changes only when a guest is added, moved, renamed or re-addressed.
- **The state** is in Semaphore's PostgreSQL, database `tfstate`, schema per module
  (`backend "pg"`); it holds the same site values, so it never goes in git.
- **What an API token can't set** (the raw `lxc.*` lines, container features other than
  nesting) is set at creation and ignored afterwards; Ansible owns it.

## Semaphore setup (once per module)

1. **A Proxmox API token for OpenTofu** (Datacenter > Permissions): a user `tofu@pve`, a token
   `semaphore` with privilege separation off, and the user given `PVEAuditor` on `/`. Reading
   the Docker VM's disks also needs `VM.Config.Disk` on `/vms/107`: a role with only that
   privilege, granted on that path. Changing guests needs more, added only once a plan is clean.
2. **A variable group** (Semaphore's sidebar; "environment" in its API) "OpenTofu: Proxmox",
   as environment variables, not extra variables:
   - variables: `PROXMOX_VE_ENDPOINT` = `https://<a Proxmox host>:8006/`,
     `PROXMOX_VE_INSECURE` = `true` (the hosts' certificates are their own cluster CA's; a
     trusted certificate replaces this before the token can change guests, `QUEUE-terraform.md`);
   - secrets: `PROXMOX_VE_API_TOKEN` = `tofu@pve!semaphore=<secret>`,
     `PG_CONN_STR` = `postgres://tfstate:<TFSTATE_DB_PASSWORD>@semaphore-postgres:5432/tfstate?sslmode=disable`,
     `TF_VAR_site` = the JSON value.
3. **A template**, type OpenTofu, repository this one, subdirectory `tofu/proxmox`, that
   environment. Each run plans first and waits for a confirmation before it applies; reject it
   to plan only.

## Checks for a change here

- `tofu fmt -check -recursive` and `tofu validate` (after `tofu init -backend=false`) pass.
- A plan from Semaphore shows only what the change meant to change. Every guest has
  `prevent_destroy`, so a change that would replace one fails the plan instead.
