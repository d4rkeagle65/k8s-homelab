# QUEUE-n8n-zammad: n8n replaces zammad-bridge

## The ask

The owner, 2026-10-29: n8n does everything `zammad-bridge` does (open, note and close
Zammad tickets from Alertmanager, Dockhand, Semaphore and Databasus, and close what's over),
querying Docker and Alertmanager itself, and the bridge is decommissioned.

## Settled

- **One container failure is one ticket without shared state.** Docker sends a die after every
  kill or stop, and Python nodes keep nothing between runs, so a kill's or stop's failure is
  held 20 s longer than a die's, and after its hold opens a ticket only if none of its key is
  open (`repeat: skip`); a kill alone still opens one. A recovery during a hold needs no
  cancel: the held run asks Docker again and finds the container running.

- **n8n writes to Zammad as a user of its own**: `ZAMMAD-BRIDGE-API-TOKEN` is a separate Zammad
  user's token (the owner, 2026-10-29), so the owner's mail for tickets n8n opens is unchanged
  (Zammad sends none to the user who made a change).

- **Staged, side by side.** One source moves at a time; its old path keeps working until its
  n8n workflow has handled real notifications. No source is ever without a way into Zammad.
- **Workflows live in git** (`n8n/workflows/`, `n8n/README.md`) and are imported by hand:
  `n8n import:workflow` deactivates what it imports, so no import at start. An edit made in
  n8n's editor is exported back over its file.
- **Import a workflow only once n8n has its credentials.** n8n drops, on import, a credential
  it doesn't have yet, silently: the node then reads "Missing required credential" and the
  workflow won't publish (the editor only hangs; the API's activate says why). A PR adding a
  credential is merged and n8n restarted before its workflow is imported; one imported too
  early is restored from git through the API (`PUT /api/v1/workflows/<id>`), as
  `n8n/README.md` says.
- **Credentials come from Key Vault**: a file each in `n8n-credentials`
  (`kubernetes/prod/apps/n8n/n8n/app/externalsecret.yaml`), imported at each start by the
  `import-credentials` init container under a fixed ID the workflows name.
- **Rules are Python Code nodes** (`language: pythonNative`, `pythonCode`), in-process data only:
  a Python Code node can't read other nodes (`$('…')`), workflow static data or the network,
  so the nodes before it gather what it needs into its input (a Set node reading each
  request's node). Tests in git (`scripts/tests/test_n8n_workflows.py`) take each Code node's
  code out of the workflow file and run it on the cases the bridge's tests cover, so the code
  tested is the code n8n runs.
- **A Code node imports only what the runners allow.** n8n's Python runner allows no
  standard-library module by default, and a container variable doesn't reach it: the
  launcher file (`/etc/n8n-task-runners.json` in the runners image) sets it in `env-overrides`.
  n8n's HelmRelease carries a copy of that file (`configMaps.runners`, read through
  `N8N_RUNNERS_CONFIG_PATH`) with `N8N_RUNNERS_STDLIB_ALLOW: "re"`; its annotation names the
  runners tag it was copied at. The tests fail on an import it doesn't allow, and when the
  runners image's tag moves past the copy's (compare the new tag's file, then move the
  annotation).
- **Requests use full responses** (`options.response.response.fullResponse`): one item with
  `body`, the array kept whole. A request that may fail without stopping the run has
  `onError: continueRegularOutput`, and the Code node reads an item with `error`, or no
  `body`, as "couldn't ask".
- **In-cluster addresses are `<service>.<namespace>`**, completed by the pod's DNS search list,
  so no address is written in a workflow. The Docker hosts' read-only proxies (port 9339)
  are Services in the `n8n` namespace: `docker-vm-1` an ExternalName (`DOCKHAND_ADDRESS`, a
  name), `docker-vm-2` and `docker-terramaster` EndpointSlices (`DOCKER_VM_2_MGMT_ADDRESS`,
  `TERRAMASTER_MGMT_ADDRESS`, IPs).
- **"Couldn't ask" is never "over"**, and a wrong guess may only keep a ticket open: until the
  alert-naming rule lives in n8n alone (stage 4), an alert ticket is over only when no active
  alert has its alert name (the ticket's service).
- **Tickets go through Zammad's ticket API, as the Checkmk endpoint made them.** That endpoint
  takes its token only in the URL's path, so n8n uses the REST API with the Zammad API
  credential instead and does what the endpoint did (`check_mk_controller.rb`): a ticket per
  host and service, in group Alerts, customer the system user (ID 1), keyed
  `preferences.check_mk` {host, service}, titled "<host> is <state>"; a repeat adds a
  Customer web article; OK or UP closes every open ticket of the key (new, open and pending
  states), with that article. So tickets the bridge opened still match and close. Every write
  is read back.
- **Webhooks use Basic Auth** with each sender's existing token as the password
  (`https://<source>:<token>@<n8n>/webhook/zammad/<source>`): Semaphore's Slack URL and
  Databasus's notifier can't send a header, and Go sends a URL's user and password as Basic
  Auth. Each workflow is a dry run in git (`write`/`close` false) until its source points
  at it.

## Stages

- [x] **1. The closing check asks Alertmanager and Docker itself** (published, running every 10
  minutes since 2026-10-29; every source answers). "Zammad: close what's
  over" gathers Zammad's open tickets, Alertmanager's active alerts and each Docker host's
  containers, and a Python Code node ("Verdict") judges each ticket.
- [ ] **2. Semaphore and Databasus** through "Zammad: tickets in" (`n8n/workflows/`): built,
  imported and published 2026-10-29; each source's test notification decided "create"; `write`
  on, and both sources point at n8n (Semaphore by #461, Databasus in its UI), 2026-10-29.
  Databasus's test message opened a ticket through n8n, read back (run 20). Left: a real
  Semaphore failure and its success, and a Databasus success closing a ticket, go through n8n;
  then the bridge's `/semaphore` and `/databasus` routes go.
- [ ] **3. Dockhand** through "Zammad: tickets in": built (this PR), with the stack check and
  the 180-second hold (a Wait node, offloaded to the database past 65 s, so it survives a
  restart). Left: after merge, n8n restarted (so it has `DockhandAlertsKV`) before the
  workflow is updated from git; a test notification; its Apprise channel moves
  (`n8n/README.md`); a real failure opens and its recovery closes; the bridge's `/dockhand`
  route goes.
- [ ] **4. Alertmanager**, with the group alerts' members from Prometheus; the alert-naming rule
  moves into n8n, and the closing check rebuilds hosts exactly. Its receiver in
  kube-prometheus-stack's `alertmanager.config` moves.
- [ ] **5. Decommission:** `kubernetes/prod/system/monitoring/zammad-bridge/`, its Ingress, its
  `cluster-secrets` tokens no longer read, `scripts/tests/test_zammad_bridge.py`, and every
  mention (`grep -rn zammad-bridge`). Move this file to `old-queues/`.

## Resume

Read the unchecked stage, `n8n/README.md` and the workflows in `n8n/workflows/`. Each stage is
one PR; the owner imports its workflow and runs it once by hand before the next.

## Done when

No source sends to the bridge, every ticket kind opens and closes through n8n, and the bridge's
folder is gone.

## Checked, negative

- n8n's Git source control and External Secrets need an Enterprise license.
- `CREDENTIALS_OVERWRITE_DATA` sets a field on every credential of a type.
