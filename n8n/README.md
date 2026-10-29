# n8n workflows

`workflows/` holds the workflows for n8n (`kubernetes/prod/apps/n8n/`), as n8n exports them.
Git is their source: a change is made here and imported, and one made in n8n's editor is
exported back here, or the next import overwrites it.

**Importing one:** in n8n, Workflows → Create → ⋯ → Import from File. Then open each node
that uses a credential and pick it, since an import carries the credential's name and not its
ID. A workflow is imported inactive: turn it on with the Active switch.

The workflows reach in-cluster services by `<service>.<namespace>`, which the pod's DNS search
list completes, so no address is written here.

| Workflow | What it does | Credentials |
|---|---|---|
| `zammad-close-whats-over.json` | Every 10 minutes, lists Zammad's open tickets, asks zammad-bridge's `/verdict` whether what opened each is over, and closes those that are, with an internal note, then reads each back. `close` in its Settings node is `false` (a dry run: they end at "Would close"). | **Zammad API**: Header Auth, name `Authorization`, value `Token token=<an agent's token with ticket.agent>` |
