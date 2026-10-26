# Claude Code on the Docker host

Claude Code with Remote Control on, in a container (`compose.yaml`): a
session you reach from claude.ai/code or the Claude app without the PC
running, and that stays up when the cluster it works on doesn't. It runs in
a tmux session named `claude` inside the container, in a clone of this repo.

## Lanes

A lane is a folder with one Claude Code process working in it, started with
Remote Control on, in its own window of that tmux session
(`bin/claude-code-start`). The clone itself is the **Main** lane; each name in
`CLAUDE_LANES` (`compose.yaml`) is a git worktree at
`.claude/worktrees/<name>`, made at start-up if it's missing, with nothing
checked out. Each is named `<REMOTE_CONTROL_NAME> - <Lane>` at claude.ai/code,
in the Claude app and on its tmux window: `k8s-homelab - Docker - Main`,
`k8s-homelab - Docker - Netbox`.

A lane's process makes a branch per change in its own folder (`git switch -c
<branch> origin/main`) and pushes it; the folder stays. So a pull request is a
branch, not a lane, and a lane is added only for another process running at
once: add its name to `CLAUDE_LANES` and redeploy. Other worktrees (Claude Code
makes its own in `.claude/worktrees/`, which `.gitignore` leaves out) get no
process. A lane whose process exits starts again ten seconds later, continuing
its newest conversation; **Ctrl-c** in its window during those ten seconds
stops it until the next start.

Every lane is a Claude Code process within the stack's `mem_limit`
(`compose.yaml`): raise the limit before adding lanes.

Everything it keeps is in `/opt/claude-code/home` on the host, which is its
home folder: its login, settings and conversations, the repo clone, and the
credentials below. None of those are in git or in Dockhand, but for its Key
Vault sign-in, which Key Vault holds and Dockhand puts in its environment.

What it can do with those credentials: administer the cluster, push to this
repo, read Proxmox, change DHCP through Kea's API, and read every secret in
Key Vault. Anyone who can steer
the Remote Control session can do the same, so consider turning on **Require
trusted devices** in your claude.ai settings.

## Before the first deploy

1. **The cluster access objects** (`kubernetes/prod/system/cluster-access/claude-code/`: a
   ServiceAccount with cluster-admin and a token Secret) are applied by Flux
   once they're on `main`.
2. **The home folder**, as root on the host. The container runs as uid 1000:

   ```sh
   install -d -m 700 -o 1000 -g 1000 /opt/claude-code/home
   ```

3. **Memory:** Claude Code wants at least 4 GB of RAM. Check the host has
   room with `free -h`.

## Deploy and log in (once)

1. In Dockhand, add this folder as a Git stack and deploy it. The first
   deploy builds the image, which takes a few minutes. On its first start
   the container clones the repo into its home folder.
2. Attach to Claude Code:

   ```sh
   docker exec -it claude-code tmux attach -t claude
   ```

3. Log in with your claude.ai account (Remote Control doesn't work with an
   API key). It shows a link: open it on any device, approve, and paste the
   code it gives you back into the terminal.
4. Trust the folder when it asks.
5. Run `/remote-control` and accept its one-time prompt. The session appears
   as `k8s-homelab - Docker - Main` at claude.ai/code and in the Claude app.
   From now on every start connects by itself. Each other lane's window shows
   the same trust prompt the first time, if its folder isn't trusted yet: move
   to it with **Ctrl-b** then its window number, and accept.
6. Detach with **Ctrl-b**, then **d**.

## Credentials

Each one goes in the home folder, readable only by the container's user.
Copy the value to the clipboard on the PC, then paste it into the helper on
the host, where it isn't shown. Never paste one into a chat. Clear the
clipboard afterwards (`Set-Clipboard -Value ' '` in PowerShell).

### Cluster access

The kubeconfig uses the `claude-code` ServiceAccount's token, not your admin
certificate, so it can be revoked on its own.

1. On the PC (PowerShell), copy the token Secret's data:

   ```powershell
   kubectl -n cluster-access get secret claude-code-token -o jsonpath='{.data}' | Set-Clipboard
   ```

2. On the host, write the kubeconfig. The API server URL is the same as in
   the PC's kubeconfig (`kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'`):

   ```sh
   docker exec -it claude-code setup-kubeconfig https://<API server>:6443
   ```

   Paste and press Enter. It ends by showing who it is:
   `system:serviceaccount:cluster-access:claude-code`.

To replace the token, delete the Secret (`kubectl -n cluster-access delete
secret claude-code-token`); Flux recreates it with a new token at its next
reconcile. Then repeat both steps. To revoke access for good, remove
`kubernetes/prod/system/cluster-access/claude-code/` from git.

### Proxmox API token

`~/.proxmox-token` holds the whole `Authorization` header value:
`PVEAPIToken=<user>@<realm>!<token name>=<secret>`. A read-only token (the
PVEAuditor role) is enough. A token of its own (Datacenter > Permissions >
API Tokens > Add) can be revoked without touching the PC's.

1. On the PC, copy the token:

   ```powershell
   (Get-Content $env:USERPROFILE\.proxmox-token -Raw).Trim() | Set-Clipboard
   ```

2. On the host, save it, then test it (`200` means it works):

   ```sh
   docker exec -it claude-code save-secret .proxmox-token
   ```

   ```sh
   docker exec claude-code sh -c 'curl -sk -o /dev/null -w "%{http_code}\n" -H "Authorization: $(cat ~/.proxmox-token)" https://<a Proxmox host>:8006/api2/json/version'
   ```

### Kea API credentials

`~/.kea-api` holds one line, `<user>:<password>`, the same as the
credentials file Kea reads (`docker/kea/README.md`).

1. On the PC, copy it:

   ```powershell
   (Get-Content $env:USERPROFILE\.kea-api -Raw).Trim() | Set-Clipboard
   ```

2. On the host, save it, then test it (a reply with `"result": 0` means it
   works). Kea's API is on the host's loopback address for this container:
   the host's management address hangs from here (`docker/kea/README.md`).

   ```sh
   docker exec -it claude-code save-secret .kea-api
   ```

   ```sh
   docker exec claude-code sh -c 'curl -s -u "$(cat ~/.kea-api)" -H "Content-Type: application/json" -d "{\"command\": \"version-get\"}" http://127.0.0.1:8000/'
   ```

### Key Vault sign-in

The `k8s-homelab-claude-code` service principal, with "Key Vault Secrets User" on
the vault: it reads every secret and writes none. Its ID, secret and tenant are
Key Vault's `ENTRA-CLAUDE-CODE-CLIENT-ID`, `ENTRA-CLAUDE-CODE-CLIENT-SECRET` and
`ENTRA-TENANT-ID`, and the stack's `.env` references them, so Dockhand sets
`AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` and `AZURE_TENANT_ID` at each deploy, with
the vault bound as the stack's secret provider (selector empty).
`scripts/keyvault_copy.py` signs in with those three when they're all set.

1. On the PC (PowerShell, at the repo root), add the three from the service
   principal's file, without showing them:

   ```powershell
   $sp = Get-Content kubernetes/.local/azure-keyvault-sp-claude-code.json -Raw | ConvertFrom-Json
   ./scripts/keyvault-secrets.ps1 ENTRA_CLAUDE_CODE_CLIENT_ID -New -Raw -NoSync -Value $sp.appId
   ./scripts/keyvault-secrets.ps1 ENTRA_CLAUDE_CODE_CLIENT_SECRET -New -Raw -NoSync -Value $sp.password
   ./scripts/keyvault-secrets.ps1 ENTRA_TENANT_ID -New -Raw -NoSync -Value $sp.tenant
   ```

2. Bind the vault as the stack's secret provider in Dockhand, and redeploy it
   (which restarts the session running in it).
3. In the container, a dry run of the copy reads the vault (`already the same`
   for each name means it works):

   ```sh
   docker exec -u claude claude-code sh -c 'cd ~/k8s-homelab && python3 scripts/keyvault_copy.py --vault <the vault>'
   ```

To replace the client secret, make a new one for the app in Entra, set
`ENTRA_CLAUDE_CODE_CLIENT_SECRET` in Key Vault to it, and redeploy.

### GitHub token

A fine-grained personal access token limited to this repo. On GitHub:
Settings > Developer settings > Personal access tokens > Fine-grained tokens
> Generate new token, with:

- **Repository access:** Only select repositories, this repo.
- **Permissions:** Contents, Read and write (Metadata, Read-only, is added
  by itself). Add Pull requests, Read and write, only if it should open
  pull requests.
- **Expiration:** your choice. When it expires, pushes fail until you make a
  new one and repeat the steps below.

Then on the host:

1. Log the GitHub CLI in. Paste the token, press Enter, then Ctrl-d:

   ```sh
   docker exec -it claude-code gh auth login --with-token
   ```

2. Let git use it for pushes over HTTPS:

   ```sh
   docker exec claude-code gh auth setup-git
   ```

3. Set the name and email its commits carry:

   ```sh
   docker exec claude-code git config --global user.name "<your name>"
   ```

   ```sh
   docker exec claude-code git config --global user.email "<your email>"
   ```

The token can push to this repo only. Whether Claude may push at all is
still up to you: that rule is in its memory, not in the token.

## Moving a conversation over from the PC

Claude Code keeps each conversation in `~/.claude/projects/<folder>/` as
`<session id>.jsonl`, plus a folder of the same name for large tool output.
`<folder>` is the project's path with every character other than a letter
or digit turned into `-`: on the PC it's under `%USERPROFILE%\.claude\projects\`
and ends in `-k8s-homelab`; here it's `-home-claude-k8s-homelab`. The
`memory` folder beside the conversations holds Claude's notes on the
project. On start, the container continues the newest conversation there.

1. End the conversation on the PC first, so the two copies don't drift
   apart.
2. On the PC, pack the conversation and the notes:

   ```powershell
   tar -czf $env:TEMP\claude-session.tgz -C "$env:USERPROFILE\.claude\projects\<PC folder>" <session id>.jsonl <session id> memory
   ```

3. Copy the archive to the host:

   ```powershell
   scp $env:TEMP\claude-session.tgz <user>@<host>:/tmp/
   ```

4. On the host, unpack it as the container's user, and mark it newest:

   ```sh
   docker exec claude-code mkdir -p /home/claude/.claude/projects/-home-claude-k8s-homelab
   ```

   ```sh
   docker exec -i claude-code tar -xzf - -C /home/claude/.claude/projects/-home-claude-k8s-homelab < /tmp/claude-session.tgz
   ```

   ```sh
   docker exec claude-code touch /home/claude/.claude/projects/-home-claude-k8s-homelab/<session id>.jsonl
   ```

5. Restart the container so it picks the conversation up. If it opens a
   different one, attach and choose it with `/resume`:

   ```sh
   docker restart claude-code
   ```

6. Delete the archive on both machines (`/tmp/claude-session.tgz` and
   `$env:TEMP\claude-session.tgz`).

The conversation moves; the PC's environment doesn't. Its history mentions
Windows paths and PowerShell, and tools that only exist in the desktop app
(its browser pane, for example) aren't available here.

## Updating

The image is built on the host from this folder, always tagged
`homelab/claude-code:local` (`pull_policy: build`), so every deploy builds it.
A deploy, by hand or the stack's weekly schedule in Dockhand (it has no
webhook, since a deploy restarts the sessions running in it), first pulls
`main` into Dockhand's clone, then builds from that commit with the build
cache: only the layers from a changed line down are rebuilt.

- **kubectl, flux, helm, OpenTofu, PowerShell:** their versions are the Dockerfile's
  `ARG`s, which Renovate bumps. A merged bump reaches the image at the next
  deploy; deploy the stack in Dockhand to have it sooner.
- **ansible-core:** the newest release at each rebuild of the Python layer
  (at least 2.19), for checking `ansible/`; Semaphore runs the playbooks.
- **Claude Code:** a package from Anthropic's apt repository that never
  updates itself. Its install layer follows the version `ARG`s, so a deploy
  after any version bump reinstalls it at the newest release on the
  Dockerfile's channel (`stable` by default); otherwise the cache keeps the
  one installed. Deploy with a rebuild without the build cache to update it
  alone.

## Removing it

1. Delete the stack in Dockhand.
2. Remove `kubernetes/prod/system/cluster-access/claude-code/` from git: Flux deletes the
   ServiceAccount, its binding and its token.
3. Revoke the GitHub token, and the Proxmox token if it had its own.
4. Change Kea's API password if you want the old one dead too.
5. Delete `/opt/claude-code` on the host.
