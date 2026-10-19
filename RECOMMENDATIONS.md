# Recommendations

A review of the repository as it stood on 2026-10-19, written for the owner to decide on. Each
item says what to change, why, and who can do it. An accepted item becomes work in `QUEUE.md`
(or its own queue file); this file isn't a queue.

## 1. Run the commit gate on pull requests, not only in the local hook

**What:** a GitHub Actions workflow that runs `tests/run-all-tests.py` on every pull request,
made a required check on `main`.

**Why:** the repository has no workflows, so a pull request is checked only if whoever made it
ran the pre-commit hook. Claude sessions do. Renovate doesn't: its pull requests never run the
suites, although `scripts/renovate_post_upgrade.py` regenerates files on its branches. A bump
that breaks generate's output or a structural test is found only after the merge, when Flux
applies it. Actions is free for a public repository.

**Notes:**

- `tests/test-identifying-data.py` needs `.secret-words`, which is never in git. Store it as a
  repository secret and write it to the file in the job, so the check runs there too. Fork pull
  requests don't receive secrets, so that check can't run on one; there are none here.
- With the check required, a red pull request can't be merged by mistake.

**Who:** a session writes the workflow as a pull request. The owner adds the secret and makes
the check required (both are repository settings).

## 2. Delete merged branches automatically

**What:** turn on "Automatically delete head branches" (Settings, General), then delete the
existing merged branches once.

**Why:** the repository has 89 branches, nearly all from merged pull requests (`media-isolation-*`,
`resource-limits-remeasure-*`, `queue-*`).

**Who:** the owner (a setting). A session can then list the merged branches for one cleanup pass.

## 3. Fewer pull requests that only edit queue files

**What:** a change carries its queue update in its own pull request (the rule in `CLAUDE.md`
already asks for it), and a pull request that touches only queue files is kept for real
re-plans.

**Why:** about one commit in five recently is queue-only, many of them `queue-*` pull requests
merged one at a time. They deploy nothing, but each is a round trip for the owner.

**Option for the owner:** let pull requests that change only Markdown outside `kubernetes/`
merge once item 1's check is green. That changes the rule "never turn on auto-merge" in
`CLAUDE.md`, so it's the owner's call alone.

## 4. One "waiting on the owner" list

**What:** keep the owner's list in `QUEUE.md`, and have `QUEUE-autonomy.md` point at it.

**Why:** both files carry one, and they overlap (Homepage's Dockhand user; Docker's
live-restore). "Say it once" (`CLAUDE.md`): with several sessions editing at once, two copies
drift apart.

## 5. Renovate

- **Clear the open pull requests regularly.** Ten are open, the oldest from 2026-10-12
  (Authentik 2026.8.4).
- **Check the two major updates the dashboard lists under "Pending Status Checks"**: postgres to
  18 and Whisparr to 3. `renovate.json` marks both for Dependency Dashboard approval, which
  lists an update under "Pending Approval" instead. Either they were approved on the dashboard,
  or the rule doesn't match their dependency names: the Renovate run's log shows which.
- **Group the media apps' minor and patch updates** (Sonarr, Radarr, Prowlarr, Lidarr,
  qBittorrent, Tdarr and its node) into one pull request, perhaps weekly, instead of one each.
- **Decide the Python version.** PR #14 moves the constraint to 3.15, while `renovate.json`
  holds `~=3.14.0`. Close it, or move the scripts and the constraint together.

## 6. Upgrades waiting

| What | Now | Available | Where |
|---|---|---|---|
| Talos | 1.14.2 | 1.14.3 | rate-limited on the dashboard |
| Kubernetes | 1.36.5 | 1.37.1 | PR #9 |
| kube-prometheus-stack | 92.3.0 | 92.9.0 | PR #13 |
| Traefik's chart | 41.7.1 | 42 (major) | rate-limited on the dashboard |

Suggested order: Talos's patch release, then Kubernetes once the add-ons support 1.37 (the check
in `QUEUE-updates.md`). Traefik 42 as a change of its own, since every route goes through it.

## 7. Docker stacks' secrets from Vaultwarden

**The question:** can Dockhand take the Docker stacks' secrets from Vaultwarden, as the cluster
does?

**What Dockhand supports** (its source, v1.0.54): ten external secret providers, resolved at
each deploy: 1Password (service account and Connect), Infisical, HashiCorp Vault, Doppler,
Bitwarden Secrets Manager, Proton Pass, Azure Key Vault, KeePassXC and Psono. None is
Vaultwarden. Its Bitwarden provider drives the `bws` CLI, which needs Bitwarden Secrets Manager
and a machine account. Vaultwarden implements neither, and its maintainers have said it won't.

**Recommended: a sync job from Vaultwarden into Dockhand's stack variables.** A small CronJob in
the cluster, which already reads Vaultwarden through External Secrets:

1. An ExternalSecret (or the existing `cluster-secrets`) holds the stacks' values, read from
   Vaultwarden.
2. The job reads each stack's variables from Dockhand
   (`GET /api/stacks/<name>/env?env=<id>`), sets the ones Vaultwarden owns, and writes the whole
   set back (`PUT` on the same path, each value `isSecret: true`). The `PUT` replaces every
   variable of the stack, so the job writes back what it read and changed: a variable it doesn't
   own keeps its value (a masked `***` is kept as stored).
3. It reads the result back, and reports any variable named in a stack's `.env` that
   Vaultwarden has no value for.

It authenticates with a Dockhand API token (`Authorization: Bearer`), for a user with no more
access than Dockhand's roles need for this (not yet checked). A changed value takes effect at the stack's next deploy.

**What stays by hand:**

- **The `vaultwarden` stack's own variables**, or a rebuild deadlocks: Vaultwarden can't supply
  the values it needs to start.
- **Anything the cluster needs before External Secrets works.** The job runs in the cluster, so a
  stack the cluster depends on to start keeps its values in Dockhand as now.

**Why this over the alternatives:**

- **A small server speaking HashiCorp Vault's KV API**, backed by the Bitwarden CLI, would let
  Dockhand resolve at deploy time with no copies. It's new code that serves secrets over HTTP,
  and every deploy would then depend on it and on the CLI being unlocked.
- **OpenBao (Vault's open-source fork) fed by External Secrets' PushSecret**, read through
  Dockhand's Vault provider: native on both sides, but a second secret store to run, back up and
  unseal.
- **KeePassXC** reads a `.kdbx` file: it would need a regular export from Vaultwarden onto the
  Docker host, a second copy of every secret on disk.

The sync keeps one store (Vaultwarden) and one reader (External Secrets), and adds a job with
tests rather than a service. Dockhand still holds a copy of each value, encrypted in its
database (AES-256-GCM), as it does now.

**Who:** a session builds the job and its tests as a pull request. The owner creates the Dockhand
user and token, and the Vaultwarden fields for the stacks' values.

## 8. Smaller things

- **The PostgreSQL containers on the Docker hosts run two major versions**: 17.11 for Kea and
  Semaphore, 18.6 for NetBox. Moving the 17s to 18 leaves one version to maintain. It's a dump and
  restore per database, not a tag change.
- **Ten queue files are open beside `QUEUE.md`**, about 2,000 lines with roughly 60 open items.
  They're current now. A file with no change for a couple of weeks is worth a pass: close it, or
  move it to `old-queues/`.
