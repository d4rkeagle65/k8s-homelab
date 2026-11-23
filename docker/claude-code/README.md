# Claude Code on the Docker host

Claude Code with Remote Control on, in containers (`compose.yaml`): sessions you
reach from claude.ai/code or the Claude app without the PC running, and that
stay up when the cluster they work on doesn't. **One container per lane**, all in
one Dockhand stack. Each runs a tmux session named `claude` with one window, in
its own clone of this repo.

## Lanes

A lane is one container running one Claude Code process with Remote Control on
(`bin/claude-code-start`), in its own clone of this repo at `~/<lane>`. Each lane
is a service in `compose.yaml`: its name appears in three places a comment marks
(the service key, `container_name` and `command`). A lane is named
`<REMOTE_CONTROL_NAME> - <Lane>` at claude.ai/code, in the Claude app and on its
tmux window, with the lane capitalised: `k8s-homelab - Docker - Main`,
`k8s-homelab - Docker - Netbox`. The stack runs two lanes, `main` and `netbox`.
The three spare lanes (`lane3`–`lane5`) are **parked** — commented out in
`compose.yaml` — because the VM OOM-killed lanes when all five ran at once beside
NetBox, Kea and its other stacks; uncomment one (below, **Adding a lane**) only
when the host has headroom.

That name is only what a lane first registers under. **To rename a lane in your
list, rename it once at claude.ai**: the web-set name syncs back to the CLI and
survives restarts and auto-titling, with no change to `compose.yaml`. To set a
different name from the start instead, add it as a second word in the lane's
`command` (`command: [lane3, Research]` → `… - Research`, while the clone stays
`~/lane3`).

Attach to a lane:

```sh
docker exec -it claude-code-<lane> tmux attach -t claude
```

**Ctrl-b** then **d** detaches. A lane's process makes a branch per change in its
own clone (`git switch -c <branch> origin/main`) and pushes it; so a pull request
is a branch, not a lane, and a lane is added only for another process running at
once. A lane whose process exits starts again ten seconds later, continuing its
newest conversation; **Ctrl-c** in its window during those ten seconds stops it
until the next start.

Each lane has **its own memory limit** (`mem_limit`, `compose.yaml`), a few times
one Claude Code process's peak. The VM has no swap, so the sum of the lanes'
limits must fit the host with room to spare: a lane that spikes is killed on its
own without taking the others down. Raise the host's memory, or lower a limit,
before adding lanes.

**Adding a lane:** copy a service block in `compose.yaml`, change the name in the
three marked places (optionally add a display name as a second word in
`command`), give it a `mem_limit` the host has room for, and redeploy. Its clone
and Remote Control session appear on its first start.

Each lane trusts its own clone folder automatically on start, so Claude Code
never stops at the "trust this folder?" prompt (`bin/claude-code-start`,
`trust_folder`).

The lanes share one home folder, `/opt/claude-code/home` on the host: the login,
settings, `~/.claude.json` and each lane's conversations (kept apart by the
clone's path). The infra credentials they work with live in
`/opt/claude-code/creds` on the host, mounted **read-only**, so no lane can
change them; they're managed on the host (**Credentials** below). The Key Vault
sign-in is the exception: Key Vault holds it and Dockhand puts it in the
environment.

What the lanes can do with those credentials: administer the cluster, push to
this repo, read Proxmox, change DHCP through Kea's API, and read every secret in
Key Vault. Anyone who can steer a Remote Control session can do the same, so
consider turning on **Require trusted devices** in your claude.ai settings.

## Before the first deploy

1. **The cluster access objects** (`kubernetes/prod/system/cluster-access/claude-code/`: a
   ServiceAccount with cluster-admin and a token Secret) are applied by Flux
   once they're on `main`.
2. **The home and credentials folders**, as root on the host. The containers run
   as uid 1000:

   ```sh
   install -d -m 700 -o 1000 -g 1000 /opt/claude-code/home
   ```

   ```sh
   install -d -m 700 -o 1000 -g 1000 /opt/claude-code/creds
   ```

3. **Memory:** each lane wants a couple of GB. Check the host has room for the
   sum of the lanes' `mem_limit`s with `free -h`; the VM has no swap.

If you're moving from the earlier single-container layout, see **Migrating from
one container** below first.

## Deploy and log in (once)

1. In Dockhand, add this folder as a Git stack and deploy it. The first deploy
   builds the image, which takes a few minutes. On its first start each lane's
   container clones the repo into `~/<lane>` in the shared home.
2. Attach to the first lane:

   ```sh
   docker exec -it claude-code-main tmux attach -t claude
   ```

3. Log in with your claude.ai account (Remote Control doesn't work with an API
   key). It shows a link: open it on any device, approve, and paste the code it
   gives you back into the terminal. The login is in the shared home, so this
   signs in every lane.
4. The session appears as `k8s-homelab - Docker - Main` at claude.ai/code and in
   the Claude app. From now on every start reconnects it by itself. **Rename it
   once from claude.ai** to lock its display name: a web-set name syncs to the
   CLI and survives Claude's auto-titling (a name set only in the CLI does not).
5. Detach with **Ctrl-b**, then **d**. Rename each other lane's session
   (`k8s-homelab - Docker - Netbox`, …) once from claude.ai the same way; the
   login is shared and each lane trusts its own folder on start, so there's
   nothing else to set up per lane.

## Credentials

The infra credentials live in `/opt/claude-code/creds` on the host and are
mounted read-only into every lane, which links each one into its usual `~/` path
on start-up (so the repo's scripts find `~/.proxmox-token`, `~/.kube/config` and
so on). Because they're read-only to the lanes, set and rotate them **on the
host**, in a short-lived container with the folder mounted read-write — the
**credentials shell**:

```sh
docker run --rm -it --network host -v /opt/claude-code/creds:/creds -e CREDS_DIR=/creds -e GH_CONFIG_DIR=/creds/.config/gh -e GIT_CONFIG_GLOBAL=/creds/.gitconfig --entrypoint bash homelab/claude-code:local
```

Every command below runs inside that shell, where the helpers and `gh` write to
`/creds`. A changed credential is picked up live (the lanes read it through the
read-only mount); a lane restart is needed only for one it didn't have before.
Copy a value to the clipboard on the PC, paste it into the helper on the host
where it isn't shown, and never paste one into a chat. Clear the clipboard
afterwards (`Set-Clipboard -Value ' '` in PowerShell). Any single-line token not
listed here is saved the same way: `save-secret <name>` in the credentials shell.

### Cluster access

The kubeconfig uses the `claude-code` ServiceAccount's token, not your admin
certificate, so it can be revoked on its own.

1. On the PC (PowerShell), copy the token Secret's data:

   ```powershell
   kubectl -n cluster-access get secret claude-code-token -o jsonpath='{.data}' | Set-Clipboard
   ```

2. In the credentials shell, write the kubeconfig. The API server URL is the
   same as in the PC's kubeconfig (`kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'`):

   ```sh
   setup-kubeconfig https://<API server>:6443
   ```

   Paste and press Enter. It ends by showing who it is:
   `system:serviceaccount:cluster-access:claude-code`.

To replace the token, delete the Secret (`kubectl -n cluster-access delete
secret claude-code-token`); Flux recreates it with a new token at its next
reconcile. Then repeat both steps. To revoke access for good, remove
`kubernetes/prod/system/cluster-access/claude-code/` from git.

### Proxmox API token

`.proxmox-token` holds the whole `Authorization` header value:
`PVEAPIToken=<user>@<realm>!<token name>=<secret>`. A read-only token (the
PVEAuditor role) is enough. A token of its own (Datacenter > Permissions > API
Tokens > Add) can be revoked without touching the PC's.

1. On the PC, copy the token:

   ```powershell
   (Get-Content $env:USERPROFILE\.proxmox-token -Raw).Trim() | Set-Clipboard
   ```

2. In the credentials shell, save it:

   ```sh
   save-secret .proxmox-token
   ```

   Test it from a lane (`200` means it works):

   ```sh
   docker exec claude-code-main sh -c 'curl -sk -o /dev/null -w "%{http_code}\n" -H "Authorization: $(cat ~/.proxmox-token)" https://<a Proxmox host>:8006/api2/json/version'
   ```

### Kea API credentials

`.kea-api` holds one line, `<user>:<password>`, the same as the credentials file
Kea reads (`docker/kea/README.md`).

1. On the PC, copy it:

   ```powershell
   (Get-Content $env:USERPROFILE\.kea-api -Raw).Trim() | Set-Clipboard
   ```

2. In the credentials shell, save it:

   ```sh
   save-secret .kea-api
   ```

   Test it from a lane (a reply with `"result": 0` means it works). Kea's API is
   on the host's loopback address for these containers: the host's management
   address hangs from here (`docker/kea/README.md`).

   ```sh
   docker exec claude-code-main sh -c 'curl -s -u "$(cat ~/.kea-api)" -H "Content-Type: application/json" -d "{\"command\": \"version-get\"}" http://127.0.0.1:8000/'
   ```

### Key Vault sign-in

The `k8s-homelab-claude-code` service principal, with "Key Vault Secrets User" on
the vault: it reads every secret and writes none. Its ID, secret and tenant are
Key Vault's `ENTRA-CLAUDE-CODE-CLIENT-ID`, `ENTRA-CLAUDE-CODE-CLIENT-SECRET` and
`ENTRA-TENANT-ID`, and the stack's `.env` references them, so Dockhand sets
`AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` and `AZURE_TENANT_ID` at each deploy, with
the vault bound as the stack's secret provider (selector empty).
`scripts/keyvault_copy.py` signs in with those three when they're all set. This
sign-in is in the environment, not a file, so it needs no credentials shell.

1. On the PC (PowerShell, at the repo root), add the three from the service
   principal's file, without showing them:

   ```powershell
   $sp = Get-Content kubernetes/.local/azure-keyvault-sp-claude-code.json -Raw | ConvertFrom-Json
   ./scripts/keyvault-secrets.ps1 ENTRA_CLAUDE_CODE_CLIENT_ID -New -Raw -NoSync -Value $sp.appId
   ./scripts/keyvault-secrets.ps1 ENTRA_CLAUDE_CODE_CLIENT_SECRET -New -Raw -NoSync -Value $sp.password
   ./scripts/keyvault-secrets.ps1 ENTRA_TENANT_ID -New -Raw -NoSync -Value $sp.tenant
   ```

2. Bind the vault as the stack's secret provider in Dockhand, and redeploy it.
3. In a lane, a dry run of the copy reads the vault (`already the same` for each
   name means it works):

   ```sh
   docker exec -u claude claude-code-main sh -c 'cd ~/main && python3 scripts/keyvault_copy.py --vault <the vault>'
   ```

To replace the client secret, make a new one for the app in Entra, set
`ENTRA_CLAUDE_CODE_CLIENT_SECRET` in Key Vault to it, and redeploy.

### GitHub token

A fine-grained personal access token limited to this repo. On GitHub:
Settings > Developer settings > Personal access tokens > Fine-grained tokens >
Generate new token, with:

- **Repository access:** Only select repositories, this repo.
- **Permissions:** Contents, Read and write (Metadata, Read-only, is added by
  itself). Add Pull requests, Read and write, only if it should open pull
  requests.
- **Expiration:** your choice. When it expires, pushes fail until you make a new
  one and repeat the steps below.

Then in the credentials shell (its `GH_CONFIG_DIR` and `GIT_CONFIG_GLOBAL` point
`gh` and git at `/creds`):

1. Log the GitHub CLI in. Paste the token, press Enter, then Ctrl-d:

   ```sh
   gh auth login --with-token
   ```

2. Let git use it for pushes over HTTPS:

   ```sh
   gh auth setup-git
   ```

3. Set the name and email its commits carry:

   ```sh
   git config --global user.name "<your name>"
   ```

   ```sh
   git config --global user.email "<your email>"
   ```

The token can push to this repo only. Whether Claude may push at all is still up
to you: that rule is in its memory, not in the token.

## Migrating from one container

The stack was one `claude-code` container with a tmux window per lane; it's now
one container per lane, and the infra credentials have moved out of the home into
a read-only folder. Once, on the host as root:

1. Create the credentials folder (see **Before the first deploy**), then move the
   credential files into it from the old home, keeping their layout:

   ```sh
   cd /opt/claude-code/home
   for f in .proxmox-token .kea-api .netbox-token .semaphore-token .ha-token .linkwarden-token .n8n-token .gitconfig; do [ -e "$f" ] && mv "$f" /opt/claude-code/creds/; done
   ```

   ```sh
   install -d -o 1000 -g 1000 /opt/claude-code/creds/.kube && [ -e /opt/claude-code/home/.kube/config ] && mv /opt/claude-code/home/.kube/config /opt/claude-code/creds/.kube/
   ```

   ```sh
   install -d -o 1000 -g 1000 /opt/claude-code/creds/.config/gh && [ -e /opt/claude-code/home/.config/gh/hosts.yml ] && mv /opt/claude-code/home/.config/gh/hosts.yml /opt/claude-code/creds/.config/gh/
   ```

2. Redeploy the stack. Each lane clones fresh into `~/<lane>` and links the
   credentials; the old `~/k8s-homelab` clone and the lanes' earlier
   conversations stay in the home but aren't used (the clone path changed). Each
   lane comes up at a new Remote Control session.
3. Each lane comes up trusting its own folder; confirm each shows online at
   claude.ai/code and rename it once from claude.ai to lock its name (**Deploy
   and log in**).

The old home's leftover clones and backed-up conversations can be deleted once
the lanes are working: they take space nothing reads.

## Moving a conversation over from the PC

Claude Code keeps each conversation in `~/.claude/projects/<folder>/` as
`<session id>.jsonl`, plus a folder of the same name for large tool output.
`<folder>` is the project's path with every character other than a letter or
digit turned into `-`: on the PC it's under `%USERPROFILE%\.claude\projects\` and
ends in `-k8s-homelab`; here a lane's clone is `~/<lane>`, so its folder is
`-home-claude-<lane>` (the Main lane is `-home-claude-main`). The `memory` folder
beside the conversations holds Claude's notes on the project. On start, a lane
continues the newest conversation in its own folder.

1. End the conversation on the PC first, so the two copies don't drift apart.
2. On the PC, pack the conversation and the notes:

   ```powershell
   tar -czf $env:TEMP\claude-session.tgz -C "$env:USERPROFILE\.claude\projects\<PC folder>" <session id>.jsonl <session id> memory
   ```

3. Copy the archive to the host:

   ```powershell
   scp $env:TEMP\claude-session.tgz <user>@<host>:/tmp/
   ```

4. On the host, unpack it as the container's user into the target lane's folder
   (here, `main`), and mark it newest:

   ```sh
   docker exec claude-code-main mkdir -p /home/claude/.claude/projects/-home-claude-main
   ```

   ```sh
   docker exec -i claude-code-main tar -xzf - -C /home/claude/.claude/projects/-home-claude-main < /tmp/claude-session.tgz
   ```

   ```sh
   docker exec claude-code-main touch /home/claude/.claude/projects/-home-claude-main/<session id>.jsonl
   ```

5. Restart that lane so it picks the conversation up. If it opens a different
   one, attach and choose it with `/resume`:

   ```sh
   docker restart claude-code-main
   ```

6. Delete the archive on both machines (`/tmp/claude-session.tgz` and
   `$env:TEMP\claude-session.tgz`).

The conversation moves; the PC's environment doesn't. Its history mentions
Windows paths and PowerShell, and tools that only exist in the desktop app (its
browser pane, for example) aren't available here.

## Updating

The image is built on the host from this folder, always tagged
`homelab/claude-code:local` (`pull_policy: build`), so every deploy builds it. A
deploy, by hand or the stack's weekly schedule in Dockhand (it has no webhook,
since a deploy restarts the sessions running in it), first pulls `main` into
Dockhand's clone, then builds from that commit with the build cache: only the
layers from a changed line down are rebuilt. Every lane shares the one image.

- **kubectl, flux, helm, OpenTofu, PowerShell:** their versions are the Dockerfile's
  `ARG`s, which Renovate bumps. A merged bump reaches the image at the next
  deploy; deploy the stack in Dockhand to have it sooner.
- **ansible-core:** the newest release at each rebuild of the Python layer (at
  least 2.19), for checking `ansible/`; Semaphore runs the playbooks.
- **Claude Code:** a package from Anthropic's apt repository that never updates
  itself. Its install layer follows the version `ARG`s, so a deploy after any
  version bump reinstalls it at the newest release on the Dockerfile's channel
  (`stable` by default); otherwise the cache keeps the one installed. Deploy with
  a rebuild without the build cache to update it alone.

## Removing it

1. Delete the stack in Dockhand.
2. Remove `kubernetes/prod/system/cluster-access/claude-code/` from git: Flux deletes the
   ServiceAccount, its binding and its token.
3. Revoke the GitHub token, and the Proxmox token if it had its own.
4. Change Kea's API password if you want the old one dead too.
5. Delete `/opt/claude-code` on the host.
