# CLAUDE.md

Flux v2 GitOps repo for a homelab Kubernetes cluster. Most of `kubernetes/` is
written by `scripts/backup.py`, which captures the live cluster and generates
the Flux files. **This repo is public.**

Read before changing anything:

- `scripts/README.md`: how the tool works, which files it owns, and recipes.
- `QUEUE.md`: the work list, most important first.
- The `CLAUDE.md` in `kubernetes/`, `docker/`, `scripts/` or `tests/` before working there.

## Layout

| Flux Kustomization (`kubernetes/flux/config/`) | Applies | Written by |
|---|---|---|
| `cluster` | `kubernetes/prod/` | generate |
| `cluster-resources` | `kubernetes/cluster/` (cluster-scoped objects) | generate |
| `cluster-meta` | `kubernetes/flux/meta/` (chart sources, vars) | hand |
| `cluster-secrets` | `kubernetes/secrets/` (the Vaultwarden ExternalSecret) | hand |
| `cluster-shared` | `kubernetes/shared/` (media base) | hand |
| `cluster-test` | `kubernetes/test/` (overlay, suspended) | hand |

`docker/<stack>/` is for the Docker host, outside the cluster, deployed by Dockhand.

## The queue lives in QUEUE.md

- **A task is not finished until QUEUE.md reflects it, in the same commit as the change.**
  Remove what is done - no "completed" section, since the CHANGELOG and history record it.
  Add what you found on the way, including problems you created: the file, a named anchor
  (a function or a distinctive string, never a line number), and what goes wrong if it is
  left. Amend what you now know differently; a half-true entry is read as current. Only
  outstanding work belongs there. Rules and conventions go in this file; an entry with no
  action in it is documentation.
- **About to start the THIRD batch of one job? Stop and write `QUEUE-<task>.md`.** It carries
  the ask, the decisions already settled, the rules concretely enough that a later batch
  matches an earlier one, where input and output live, a checklist, how to resume, "done
  when", and the checks that came back negative. Design it so a half-finished pass is still
  valid. When done, move it to `old-queues/` with a row in that folder's README - never delete.

## Docs say what is true now

- **History lives in the commit message and CHANGELOG.md, not in the docs.** A sentence that
  narrates what went wrong either stops a future edit reintroducing the bug - make it a rule
  here - or it does not, and it goes. Code comments too: keep the invariant, drop the story.
  The test for what survives: would a reader who does not know the history still need it?
- **Say it once.** An explanation repeated across four files is four things that must stay
  true. Put it in one place and point at it.
- **A rule scoped to one folder lives in that folder's CLAUDE.md**, which loads only when work
  touches that folder. Split by scope, never to get under the budget.

## How this is built

- **Commit without running the suite first - the pre-commit hook runs it**, and a failure
  aborts cleanly. While iterating, run only the suite covering what you touched. Never
  `--no-verify`: fix the cause, or raise it.
- **Run `tests/run-all-tests.py --include-environment` when you change which modules,
  packages or tools a script uses**, and list what it needs in a `# /// script` block or in
  `test_config.toml` Prerequisites. The gate leaves the environment suites out because they
  track the machine, and only they catch a package that is missing or below its minimum.
- **Windows and Linux differ in ways that bite, so proving a change on one proves less than it
  looks.** On Windows, `open()` with no `encoding=` reads and writes the locale's code page,
  and so does `subprocess` with `text=True`; a text-mode write turns `\n` into `\r\n` unless
  given `newline="\n"`; a file another process holds open cannot be renamed or deleted; paths
  compare without case; `os.killpg` and `signal.SIGKILL` do not exist. Each target Python
  differs too: syntax or a standard-library name one adds is an error on the one before.
- **Use the Edit/Write tools for anything containing backslashes.** Quoted heredocs halve
  `\\`, and `sed` reads `\t` and `\c` as escapes, leaving an invisible control character.
- **A command run in the background pipes its output through `tee`** (`<command> 2>&1 | tee
  <file>`), so it streams live to the background-tasks panel and a copy stays to read after;
  take its exit code from `${PIPESTATUS[0]}`, not `$?`, which is `tee`'s.
- **`.gitignore` owns the exclusion list.** Select files through git (`git ls-files`),
  never through a literal list of folders that a new ignore rule leaves stale.

## What a script must never do

- **A check that failed is not a check that answered.** A test has three outcomes - yes, no,
  and could-not-answer - and the third stops the change instead of acting on an unknown
  state. A refused read is not an absent value; a failed query is not an empty result.
- **"Applied" means verified.** Read the state back after writing it. A call can report
  success and store nothing, and a write that raises nothing has not shown that it took.
- **Ask what a thing looks like when it is present and doing nothing.** Disabled, audit-only,
  scoped to an empty group: each is present exactly as the working version is. Read the mode,
  the scope or the enabled flag, never mere existence.
- **Name the real cause.** A failure caused by the script, the machine or a missing
  prerequisite is not a finding about the target. When a prerequisite is missing, report the
  gap rather than doing the half that would succeed.
- **A switch decides whether a script CHANGES something, not whether it LOOKS.** Every path a
  switch turns off still reports what it found. Silence reads as "we did not look".
- **Exactly one place owns a setting.** With two owners the second silently overwrites the
  first, and the output shows whichever ran last.
- **Nothing environment-specific is hardcoded.** Values that belong to one machine, customer
  or deployment come from configuration.
- **A change that is hard to reverse is config-gated and off in the sample config.**

## Project-specific rules

- **No real values in git, and a new variable reaches the cluster before what uses it.** No
  domains, IPs, hostnames or secrets in files, commit messages or PR text: use `${VARIABLE}`s,
  which Flux fills from `cluster-secrets` (built by External Secrets from a Vaultwarden item;
  `kubernetes/secrets/`) and `cluster-settings` (`kubernetes/flux/meta/vars/`). Real values
  live only in Vaultwarden, the gitignored `kubernetes/.local/`, and `.secret-words` for the
  hook. An undefined variable fails its Flux Kustomization, so only the owner adds one, before
  anything using it is pushed; an empty value is fine.
- **Work on a branch, never directly on `main`; a merge is a deploy.** Flux applies whatever
  reaches `main`. The owner reviews the full diff first, and `main` is pushed only when the
  owner asks for that push.

## In a cloud session

The cloud has a fresh clone and no cluster access: no `kubectl`, `helm` or `flux` against the
cluster, and no `kubernetes/.local/`, `docs/` or `kubernetes/raw/`. `generate` and the tests
work offline.

- **Don't run `capture` or `all` there.** They read the live cluster.
- **When you're done, commit to your own branch, push that branch, and end with a summary.**
  Don't merge, push to `main` or open a PR unless asked; the owner merges locally after
  checking the change against the live cluster. The summary says what changed, what to check
  against the live cluster (for example, which Flux Kustomizations would change), and whether
  `scripts/` changed.
