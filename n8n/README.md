# n8n workflows

`workflows/` holds the workflows for n8n (`kubernetes/prod/apps/n8n/`), as n8n exports them.
Git is their source: a change is made here and imported, and one made in n8n's editor is
exported back here, or the next import overwrites it.

**Importing one:** in n8n, Workflows → Create → ⋯ → Import from File, then Publish. Only once
n8n has every credential it names: an import drops a credential n8n doesn't have yet, without a
word, and the workflow then won't publish (the editor only hangs). So a PR adding a credential
is merged, and n8n restarted, before its workflow is imported; one imported too early is put
back from git through n8n's API (`PUT /api/v1/workflows/<id>`). (n8n's `import:workflow` would deactivate it at
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
| `zammad-close-whats-over.json` | Every 10 minutes, lists Zammad's open tickets, asks each Alertmanager pod for its active alerts (they share none; either failing makes alert tickets unknown) and each Docker host's proxy for its containers (`docker-vm-1.n8n` and so on, `kubernetes/prod/apps/n8n/n8n/app/docker-hosts.yaml`), and its Python "Verdict" judges each ticket the alerts made: over, ongoing or unknown. An alert's ticket is over once no active alert's labels rebuild to its host, with the rule "Zammad: tickets in" opens it with (the tests hold both nodes to the same lines). It closes those over, with an internal note, then reads each back. `close` in its Settings node is `false` (a dry run: they end at "Would close"). | **Zammad API** (`ZammadApiFromKV1`): Header Auth, `Authorization: Token token=…` from `ZAMMAD-BRIDGE-API-TOKEN`, an agent's token with ticket.agent |
| `zammad-tickets-in.json` | Opens, notes and closes Zammad tickets from each source's webhook (`/webhook/zammad/<source>`, Basic Auth): a Python node per source turns its notification into an event (host, service, state, text), and "Decide" does what Zammad's Checkmk endpoint did, through Zammad's ticket API: a failure opens a ticket in Alerts keyed `preferences.check_mk` {host, service}, or notes the open one; a recovery closes them. Each write is read back ("Verify" fails the run otherwise). `write_sources` in its Settings node lists the sources whose decisions are written; any other's end at "Would write" (a dry run). Sources, all written: Semaphore, Databasus, Dockhand, and Alertmanager (`/webhook/zammad/alertmanager`, Header Auth, a bearer token; it answers after its last node, so a failed write is a 500 Alertmanager sends again; a group alert's members down come from Prometheus). Dockhand's answers at once, and a stack container's failure waits out a hold (a Wait node, 180 s, 200 for a kill or stop, whose die follows) and is asked of the Docker hosts again: one running again by then opens nothing. Dockhand's channel: Settings, Notifications, an Apprise channel `jsons://dockhand:<DOCKHAND-ALERT-TOKEN>@n8n.<domain>/webhook/zammad/dockhand`. | **Zammad API**; **Semaphore alerts**, **Databasus alerts** and **Dockhand alerts** (`SemaphoreAlertKV`, `DatabasusAlertKV`, `DockhandAlertsKV`): Basic Auth, users `semaphore`, `databasus` and `dockhand`, passwords `SEMAPHORE-ALERT-TOKEN`, `DATABASUS-ALERT-TOKEN` and `DOCKHAND-ALERT-TOKEN`; **Alertmanager** (`AlertmanagerKV01`): Header Auth, `Authorization: Bearer …` from `ALERTMANAGER-N8N-TOKEN` |

## Testing every path into Zammad

`scripts/alerting_test.py`, run from Semaphore as **"Tool: Alerting test (script)"** (by hand,
after changing any part of alerting), sends each source a failure and its recovery and checks
through Zammad's API that each opened its ticket and closed it, in about ten minutes:

| Leg | Sent | Proves |
|---|---|---|
| alertmanager | a marker line to Loki's push route, which Loki's `HomelabAlertingTest` rule (`monitors/app/configmap-loki-rules-homelab.yaml`) fires on | Loki's ruler, both Alertmanager pods' routing, n8n, Zammad |
| semaphore, databasus, dockhand | a failure and a recovery in each sender's format, to its n8n webhook with its token | n8n and Zammad |

Each run's tickets carry its time ("Semaphore Alerting test 20261031…"), and mail as any
ticket does. `scripts/tests/test_alerting_test.py` runs n8n's own Code nodes on the test's
alerts, so the keys it waits for are the ones n8n opens.

**The template:** a Python template, script `scripts/alerting_test.py`, no inventory, and the
variable group **"Alerting Test"**, which the Key Vault sync makes and keeps
(`scripts/semaphore_variable_groups.yaml` lists what it holds; QUEUE-semaphore-variables.md).
`ALERTING_TEST_LEGS` (comma-separated) runs only some legs.
