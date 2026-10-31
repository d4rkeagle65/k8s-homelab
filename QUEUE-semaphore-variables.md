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

- [ ] 1. The sync (this PR, after #480, whose "Alerting Test" it declares): the script, its
  config, its tests; no group managed but "Alerting Test", which is new. Left, the owner's: the service principal
  (done: commands given 2026-10-31), a Manager's API token, the "Key Vault Sync" group, and the
  template; then one run, whose report lists every group's variable names.
- [ ] 2. "Alerting Test" managed: the first group written (it's new, nothing to lose), then
  "Tool: Alerting test (script)" (`n8n/README.md`) attached to it and run.
- [ ] 3. Each existing group, one PR each: its values into Key Vault where missing, declared,
  its report read, then `managed: true`. The report of step 1 lists what each holds.
- [ ] 4. A schedule (daily), once several groups are managed, so a Key Vault change reaches
  Semaphore without a run by hand.

## Resume

Read the checklist and `scripts/semaphore_variable_groups.yaml`; run the sync's template and
read its report.

## Done when

Every group but "Key Vault Sync" is declared and managed, and adding a template's variables is
a line in the config and a Key Vault secret.

## Renewal

`semaphore-keyvault-reader`'s secret expires 2028-10-31 (`--years 2`): `az ad sp credential
reset --id <client id> --years 2`, the new value into Key Vault and "Key Vault Sync". When it
expires the sync fails, saying Key Vault refused it.
