# n8n workflows

`workflows/` holds the workflows for n8n (`kubernetes/prod/apps/n8n/`), as n8n exports them.
Git is their source: a change is made here and imported, and one made in n8n's editor is
exported back here, or the next import overwrites it.

**Importing one:** in n8n, Workflows → Create → ⋯ → Import from File. A workflow is imported
inactive: turn it on with the Active switch. (n8n's `import:workflow` would deactivate it at
every start, so workflows aren't imported automatically.)

**Credentials** come from Key Vault, never the editor: each is a file in
`kubernetes/prod/apps/n8n/n8n/app/externalsecret.yaml`, imported at every n8n start by its
`import-credentials` init container, under a fixed ID the workflows name. A changed value in Key
Vault reaches n8n at its next restart (`kubectl -n n8n rollout restart deploy/n8n`, after the
ExternalSecret's hourly refresh or a forced sync); that container's log says what it imported.

A Python Code node's code is tested in git (`scripts/tests/test_n8n_workflows.py` runs it as
n8n's runner does), and imports only the standard-library modules the runners' launcher file
allows (`N8N_RUNNERS_STDLIB_ALLOW` in n8n's HelmRelease, `configMaps.runners`): the runner
allows none by default, and a container variable can't change that.

The workflows reach in-cluster services by `<service>.<namespace>`, which the pod's DNS search
list completes, so no address is written here.

| Workflow | What it does | Credentials (Key Vault) |
|---|---|---|
| `zammad-close-whats-over.json` | Every 10 minutes, lists Zammad's open tickets, asks Alertmanager for its active alerts and each Docker host's proxy for its containers (`docker-vm-1.n8n` and so on, `kubernetes/prod/apps/n8n/n8n/app/docker-hosts.yaml`), and its Python "Verdict" judges each ticket the alerts made: over, ongoing or unknown. It closes those over, with an internal note, then reads each back. `close` in its Settings node is `false` (a dry run: they end at "Would close"). | **Zammad API** (`ZammadApiFromKV1`): Header Auth, `Authorization: Token token=…` from `ZAMMAD-BRIDGE-API-TOKEN`, an agent's token with ticket.agent |
| `zammad-tickets-in.json` | Opens, notes and closes Zammad tickets from each source's webhook (`/webhook/zammad/<source>`, Basic Auth): a Python node per source turns its notification into an event (host, service, state, text), and "Decide" does what Zammad's Checkmk endpoint did, through Zammad's ticket API: a failure opens a ticket in Alerts keyed `preferences.check_mk` {host, service}, or notes the open one; a recovery closes them. Each write is read back ("Verify" fails the run otherwise). `write` in its Settings node is `false` (a dry run: decisions end at "Would write"). Sources so far: Semaphore, Databasus. | **Zammad API**; **Semaphore alerts** and **Databasus alerts** (`SemaphoreAlertKV`, `DatabasusAlertKV`): Basic Auth, users `semaphore` and `databasus`, passwords `SEMAPHORE-ALERT-TOKEN` and `DATABASUS-ALERT-TOKEN` |
