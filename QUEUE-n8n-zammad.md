# QUEUE-n8n-zammad: n8n replaces zammad-bridge

## The ask

The owner, 2026-10-29: n8n does everything `zammad-bridge` does (open, note and close
Zammad tickets from Alertmanager, Dockhand, Semaphore and Databasus, and close what's over),
querying Docker and Alertmanager itself, and the bridge is decommissioned.

## Settled

- **Staged, side by side.** One source moves at a time; its old path keeps working until its
  n8n workflow has handled real notifications. No source is ever without a way into Zammad.
- **Workflows live in git** (`n8n/workflows/`, `n8n/README.md`) and are imported by hand:
  `n8n import:workflow` deactivates what it imports, so no import at start. An edit made in
  n8n's editor is exported back over its file.
- **Credentials come from Key Vault**: a file each in `n8n-credentials`
  (`kubernetes/prod/apps/n8n/n8n/app/externalsecret.yaml`), imported at each start by the
  `import-credentials` init container under a fixed ID the workflows name.
- **Rules are Python Code nodes** (`language: pythonNative`, `pythonCode`), in-process data only:
  a Python Code node can't read other nodes (`$('…')`), workflow static data or the network,
  so the nodes before it gather what it needs into its input (a Set node reading each
  request's node). Tests in git (`scripts/tests/test_n8n_workflows.py`) take each Code node's
  code out of the workflow file and run it on the cases the bridge's tests cover, so the code
  tested is the code n8n runs.
- **No imports in a Code node unless the runners allow them.** n8n's Python runner allows no
  standard-library module by default (`N8N_RUNNERS_STDLIB_ALLOW` is set to "" in the launcher
  file's `env-overrides`, `/etc/n8n-task-runners.json` in the runners image; a container
  environment variable doesn't reach it). A stage that needs `re` overrides that file from a
  ConfigMap, copied from the image's at the running tag; the test fails on an import the
  override doesn't allow.
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
- **Tickets keep the bridge's fields** (host, service, state, text through Zammad's Checkmk
  endpoint), so tickets the bridge opened still match and close.

## Open, decided at their stage

- **How n8n reaches the Checkmk endpoint** (stage 2): its token is in the URL's path, which no
  credential type fills. Options: Zammad's REST API with the agent token (then tickets carry
  the key some other way), or the URL as a credential field an expression can't read. Read
  Zammad's `check_mk` controller first.
- **Dockhand's "a die and a kill hold once"** (stage 3): Python has no static data. JS's
  `$getWorkflowStaticData('global')` is saved only by non-manual runs (unverified that the JS
  Code node exposes it); or ask Zammad whether the ticket is already open.

## Stages

- [x] **1. The closing check asks Alertmanager and Docker itself** (left: the owner imports it and
  runs it once; the Docker Services resolve and answer). "Zammad: close what's
  over" gathers Zammad's open tickets, Alertmanager's active alerts and each Docker host's
  containers, and a Python Code node ("Verdict") judges each ticket as `/verdict` does. The
  bridge's `/verdict` goes; the Docker Services land in `n8n`.
- [ ] **2. Semaphore and Databasus** open and close tickets through n8n Webhook triggers (Header
  Auth from Key Vault), then each source's URL moves (Semaphore's `SEMAPHORE_SLACK_URL`,
  `docker/secret-names.yaml`; Databasus's notifier), then the bridge's routes go.
- [ ] **3. Dockhand**, with the stack check and the 180-second hold (a Wait node, offloaded to
  the database past 65 s, so it survives a restart), then its Apprise channel moves.
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
