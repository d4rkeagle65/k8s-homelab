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

The workflows reach in-cluster services by `<service>.<namespace>`, which the pod's DNS search
list completes, so no address is written here.

| Workflow | What it does | Credentials (Key Vault) |
|---|---|---|
| `zammad-close-whats-over.json` | Every 10 minutes, lists Zammad's open tickets, asks zammad-bridge's `/verdict` whether what opened each is over, and closes those that are, with an internal note, then reads each back. `close` in its Settings node is `false` (a dry run: they end at "Would close"). | **Zammad API** (`ZammadApiFromKV1`): Header Auth, `Authorization: Token token=…` from `ZAMMAD-BRIDGE-API-TOKEN`, an agent's token with ticket.agent |
