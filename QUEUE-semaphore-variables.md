# QUEUE-semaphore-variables: Semaphore's variable groups from Key Vault

## The ask

The owner, 2026-10-31: "We have so many variable groups and keep having to add more. I'd love
to have that automated." Semaphore's variable groups are filled by hand today (12 groups, 27
templates); each new template has meant another group typed in from Key Vault or Vaultwarden.

## Settled

- **A sync owns the groups: git says what each holds, Key Vault holds the values.**
  `scripts/semaphore_variable_groups.yaml` declares each group's extra variables and
  environment variables, each from one source: `keyvault: <SECRET-NAME>` (a secret by default),
  `template: "https://n8n.{BASE-DOMAIN}"` (Key Vault entries filled into text, so no domain or
  address is in git), or `value:` (a plain setting naming nothing). `scripts/semaphore_variable_groups.py`
  makes each group match, through Semaphore's API, as Semaphore's "Sync: Key Vault → Semaphore
  (script)".
- **Written only when the file says `managed: true`.** Every run reports every declared group's
  differences (by name, never a value) and every undeclared group's variable names; it writes
  only managed groups. A group is marked managed in its own PR, after its report reads right.
  A managed group is made exactly as declared: an undeclared variable or secret in it is
  removed. Groups the file doesn't declare are left alone (one owner per group).
- **No partial writes, and reads that failed aren't empty values.** A group with any value Key
  Vault didn't give is not written, and the run fails naming it. Each written group is read
  back: its extra and environment variables equal to the declared, its secrets exactly the
  declared names and types. A secret's value can't be read back (Semaphore never returns
  one), so a secret is rewritten at every run, and the report says "set".
- **The last hand-filled group is the sync's own, "Key Vault Sync"**: `AZURE_TENANT_ID`,
  `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` (the `semaphore-keyvault-reader` service principal,
  "Key Vault Secrets User" on the vault only; its three values also in Key Vault as
  `SEMAPHORE-KEYVAULT-*`, for safekeeping), `AZURE_KEYVAULT_URL`, `SEMAPHORE_URL` and
  `SEMAPHORE_SYNC_TOKEN` (a Manager's API token), all environment variables, secret where
  secret. The sync refuses to declare it, since it would rewrite its own credentials.
- **Standard library and `ruamel.yaml`**, as the NetBox sync (Semaphore's Python has both): Key
  Vault through its REST API (a client-credentials token for `https://vault.azure.net/.default`),
  Semaphore through its own (`/api/project/<id>/environment`, a bearer token).

## Checked, negative

- **Semaphore's own Azure Key Vault secret storage** (`azure_kv`, from v2.18): in the open-source
  build it's a stub (`pro/services/server/access_key_serializer_azure_kv.go`: every call returns
  nothing and no error), so a task would run with empty secrets and nothing would say so. Pro only.
- **OpenTofu's Semaphore provider**: keeps every secret's value in its state database.
- **Forwarding Key Vault values through the Semaphore stack** (`SEMAPHORE_FORWARDED_ENV_VARS`):
  reaches every task, and still meant a stack edit per value.

## Checklist

- [x] 1. The sync (#484), run as "Sync: Key Vault → Semaphore (script)" (template 38; the owner
  made "Key Vault Sync" and it, 2026-10-31). Task 1447 made "Alerting Test", read back.
- [x] 2. "Alerting Test" managed: made by task 1447. Left: "Tool: Alerting test (script)"
  (`n8n/README.md`) attached to it and run once.
- [ ] 3. Each existing group, one PR each: its values into Key Vault where missing, declared,
  its report read, then `managed: true`. What each held at task 1447 (names only):

  | Group | Extra variables | Environment | Secrets |
  |---|---|---|---|
  | Debian Host Baseline | admin_ssh_keys, admin_sudo_nopasswd, admin_user, host_alloy, host_exporters, loki_push_url, management_network | | env loki_push_password |
  | Home Assistant IoT | | | env HA_IOT_AREAS, HA_TOKEN, HA_URL, NETBOX_API, NETBOX_TOKEN, TPLINK_PASSWORD, TPLINK_USERNAME |
  | Netbox Sync | | KEA_URL, PROXMOX_URL | env GITHUB_TOKEN, KEA_API, NETBOX_WRITE_TOKEN, PROXMOX_TOKEN |
  | OpenTofu: Cloudflare | | | env CLOUDFLARE_API_TOKEN, PG_CONN_STR, TF_VAR_dns |
  | OpenTofu: Proxmox and Talos | | PG_CONN_STR, PROXMOX_VE_ENDPOINT, TF_VAR_netbox_url | env PG_CONN_STR, PROXMOX_VE_API_TOKEN, TF_VAR_netbox_token, TF_VAR_site, TF_VAR_talos_secrets |
  | OpenTofu: Tailscale | | PG_CONN_STR, TF_VAR_netbox_url | env TAILSCALE_OAUTH_CLIENT_ID, TAILSCALE_OAUTH_CLIENT_SECRET, TF_VAR_netbox_token |
  | Pi-Hole Settings | pihole_settings | | |
  | Proxmox Settings | acme_cf_zone_id, acme_domain, acme_email, lxc_timezone, nas_block_initialise, nas_block_portal, nas_block_target, ntp_serve_networks | | var acme_cf_token |
  | Synology Baseline, Terramaster Baseline | | | env HAWSER_TOKEN (each its own) |
  | Windows Updates | | WINDOWS_ANSIBLE_USER | env WINDOWS_ANSIBLE_PASSWORD |
  | k8s and Talos Connection | | | env K8S_API_URL, K8S_CA_B64, K8S_TOKEN, TALOSCONFIG_B64 |

  `PG_CONN_STR` is both a plain variable and a secret in "OpenTofu: Proxmox and Talos": which
  one a task sees is Semaphore's choice; declaring the group keeps one (a secret, since it
  holds a password). `TF_VAR_site` and `TF_VAR_dns` are JSON of site facts, which NetBox or
  Key Vault templates may replace when their groups move.
  - [ ] Every group declared unmanaged (#488), each value with its Key Vault name; extra
    variables as JSON (`json: true`). Their values exist only in Semaphore, which never
    returns a secret, so a one-time import copies them out from inside a task:
    `ansible/semaphore-tool-keyvault-import.yml`, "Tool: Variable Group → Key Vault (one
    time)", with "Key Vault Sync" and the group to import attached (it asks Semaphore which; a
    survey `import_group` overrides); one group a run. Its dry run reads Key Vault and says
    what it would write, or finds the same or different. It never overwrites. The owner's: "Key Vault Secrets Officer"
    for `semaphore-keyvault-reader` for the import, the template, a dry run, then a run per
    group (the cluster access's four the owner writes from the cluster instead), then the
    role removed. Then a sync run should report nothing missing, and each group goes managed
    in its own PR, after its check: the NAS baselines' dry runs report Hawser unchanged, a
    Proxmox update's dry run passes its cluster checks, each OpenTofu module's plan shows no
    changes.
- [ ] 4. A schedule (daily), once several groups are managed, so a Key Vault change reaches
  Semaphore without a run by hand.

## Resume

Read the checklist and `scripts/semaphore_variable_groups.yaml`; run the sync's template and
read its report.

## Done when

Every group but "Key Vault Sync" is declared and managed, and adding a template's variables is
a line in the config and a Key Vault secret.

## After the import

Remove the write role (`az role assignment delete --assignee <client id> --role "Key Vault
Secrets Officer" --scope <vault id>`), delete the import template, and delete
`ansible/semaphore-tool-keyvault-import.yml` and `scripts/semaphore_keyvault_import.py`
with their test, so nothing can write Key Vault from Semaphore again.

## Renewal

`semaphore-keyvault-reader`'s secret expires 2028-10-31 (`--years 2`): `az ad sp credential
reset --id <client id> --years 2`, the new value into Key Vault and "Key Vault Sync". When it
expires the sync fails, saying Key Vault refused it.
