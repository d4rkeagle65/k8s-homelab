---
name: semaphore
description: Start a Semaphore task (a dry run, an OpenTofu plan, a read-only "Tool: Run Command" check, the NetBox sync) and read its log and result, or read an earlier task by its ID. Use whenever work needs something run on the hosts through Semaphore, or asks what a Semaphore task printed.
---

# Running something on Semaphore

Use `scripts/semaphore_run.py` from the repo root. It knows the API: how survey values are
sent, the dry run in `params`, waiting for the task, the log. Don't work out the API with
`curl`; if the script can't do what's needed, change the script (its tests are
`scripts/tests/test_semaphore_run.py`).

Which runs I may start myself, and which only the owner asks for, is the Semaphore rule in
the root `CLAUDE.md`. The script never confirms a plan; a plan it started that ends waiting
for confirmation, it rejects.

## Before the first run

It needs Semaphore's address and an API token, from `SEMAPHORE_URL` and `SEMAPHORE_TOKEN`,
or else `~/.semaphore-url` and `~/.semaphore-token`. On the Docker host's Claude both files
are in the container's home folder (`docker/claude-code/README.md`, "Semaphore API token").
If neither is set, the script says so and starts nothing: tell the owner which is missing,
never look for a token elsewhere. A cloud session has no route to Semaphore at all.

## Recipes

```sh
python3 scripts/semaphore_run.py templates                  # every template and its survey (* = required)
python3 scripts/semaphore_run.py templates "run command"    # those whose name holds the text

# A dry run (the default) of an Ansible template:
python3 scripts/semaphore_run.py run "Update: Proxmox Hosts"

# A read-only command on chosen hosts. A real run, since this template's dry run only lists
# the hosts. A command holding {{ }} goes with -b, which sends run_command_b64:
python3 scripts/semaphore_run.py run "Tool: Run Command" --real \
  -v target_hosts=<hosts or groups> -v "run_command=uptime"
python3 scripts/semaphore_run.py run "Tool: Run Command" --real \
  -v target_hosts=<hosts> -b "run_command=docker ps --format '{{.Names}}'"

# An earlier task, or one still running:
python3 scripts/semaphore_run.py show <task ID>
python3 scripts/semaphore_run.py show <task ID> --wait
```

- A template that isn't Ansible (a Python script, OpenTofu) has no dry run in Semaphore, so
  the script refuses it without `--real`. Its own survey variable, when it has one (a
  `DRY_RUN`), is what keeps it read-only: read the template's survey with `templates` first.
- Give a template by its exact name; `templates` lists them. A misspelt name gets the near
  ones suggested.
- Exit codes: 0 success, 1 the task failed (or was stopped or rejected), 2 not started or
  Semaphore didn't answer (never read that as "nothing to do"), 3 still running at
  `--timeout` (900 s by default; the task's ID is printed for `show --wait`).
- Quote the task's ID in what you report, so the owner can open its log in Semaphore.
