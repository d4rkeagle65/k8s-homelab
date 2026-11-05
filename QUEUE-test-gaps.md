# Test gaps: script logic no test checks

**The ask** (the owner, 2026-11-03): break-test every test, then "do the sweep of the other
pytest tests", then work through every gap it found (2026-11-04: "work through all of the
items"). Each item: tests that go red when its logic breaks, proven by breaking it.

## Settled

- **The sweep.** For each function in a script the suite reaches, insert `return None` as its
  first statement (after a docstring), run the pytest files that reach it (by coverage, or that
  name its file), and expect red. A function that stays green is one no test checks. Then
  targeted breaks for the logic that matters (a check removed, a comparison flipped, an order
  reversed), each expected red.
- **Run every mutation with `PYTHONDONTWRITEBYTECODE=1` and no `__pycache__` left over.**
  Python trusts a cached `.pyc` by the source's mtime, in whole seconds, and its size: two
  same-length mutations of one file within a second run the first one's bytecode. The first
  sweep's results were wrong in 23 places for this.
- **Out of scope, as a survivor that is no gap:** the HTTP or subprocess call a test replaces
  with a fake (`call`, `http`, `run`, `kubectl`), logging, an HTTP handler that only serves what
  a tested function renders, a server loop, and a `main()` that only parses arguments and calls
  tested functions. A `main()` that holds its own checks is in scope (gen-config's was).
- **Out of scope, as files:** `docker/netbox/config/*.py` (NetBox settings, no logic),
  `docker/netbox/custom_pipeline.py` (24 lines on NetBox's own models, which aren't here), and
  `scripts/backup.py` (the entry point, 17 lines).
- **How a test is written** (`tests/CLAUDE.md` applies throughout):
  - the script loaded by path (`importlib.util.spec_from_file_location`) and its boundary
    replaced on the loaded module, never a copy of its code;
  - a package the script's own image installs but this machine may lack (`pymysql`, `yaml`)
    stubbed in `sys.modules` for the load;
  - addresses from RFC 5737, names from `example.org`/`example.invalid`;
  - credential-shaped values built at run time or held in a constant, so the commit hook's
    secret shapes never match the test's text.
- **A real bug found is fixed in the same pull request** and named in it, as #567's was.
- **Moving a closure out of `main()` to test it is allowed**, its body unchanged but for the
  arguments it closed over, checked by diffing each function against its original and by
  pyflakes (#570).

## Checklist

Done: the hygiene scans' multi-document blind spot (#562); `secretscan.py`'s detectors and the
env-var miss beside `valueFrom` (#567); `talos/gen-config.py` (#569); `netbox_seed.py` (#570);
`organizr-sync.py` (#571); `flux.py` and `generate.py`, with generate reproducing the committed
tree (#576).

**Batch A, single functions in tested scripts:**
- [x] `scripts/limitrange_jobs.py`: `peaks`, `hook_jobs`, `main` (10 of 10; 9 targeted)
- [x] `docker/kea/ha_exporter.py`: `collect` and its pages (8 of 9; left: `log_message`; 3 targeted)
- [x] `scripts/k8s_backup/filetracker.py`: the whole tracker (8 of 8; 6 targeted)
- [x] `scripts/k8s_backup/varsub.py`: `load_settings` (22 of 22)
- [x] `scripts/keyvault_facts.py`: `choices` (38 of 38; 3 targeted)
- [x] `scripts/semaphore_variable_groups.py`: `put` removed, uncalled since #502 and #535
- [x] `scripts/k8s_backup/cli.py`: `_activate_pre_commit_hook`, `_check_tools` (left: argument
      parsing, the report's printing, `main`; 3 targeted)
- [x] `scripts/k8s_backup/secretscan.py`: the IP purposes, and a self-test for the
      high-confidence shapes, which only other files' tests had reached (19 of 19; 3 targeted)
- [x] `scripts/k8s_backup/capture.py`: `_summarize_ip_inventory` (2 targeted); and
      `_collect_ip_inventory` now reads every document, as the IP inventory left out every
      multi-document file

**Batch B, the cluster's own scripts with no test:**
- [x] `kubernetes/prod/system/monitoring/garage-backup-exporter/app/garage-backup-exporter.py` (10 of 13;
      left: the refresh loop, its handler, `log_message`; 8 targeted, the signature pinned)
- [x] `kubernetes/prod/services/renovate/renovate/app/config/report-to-metrics.py` (4 of 4; 5 targeted)
- [x] `kubernetes/prod/apps/media/media-sync/app/sync/media-sync.py` (14 of 15, left: `http`; 10
      targeted); a dry run failed whenever Seerr lacked a listed server: fixed
- [x] `kubernetes/prod/apps/grimmory/grimmory/app/sync/grimmory-sync.py` (16 of 17, left: `call`; 11 targeted)
- [x] `kubernetes/prod/apps/grimmory/grimmory/app/authors/grimmory-authors.py` (3 of 4, left: `call`; 5 targeted)
- [x] `kubernetes/prod/apps/grimmory/grimmory/app/relink/grimmory-relink.py` (4 of 4; 15 targeted; its SQL
      on SQLite through a pymysql stand-in)
- [x] `kubernetes/prod/apps/media/rreading-glasses/app/repair/rreading-glasses-repair.py` (17 of 18, left:
      `http`; 20 targeted, and the start's budget check, an equivalent: the first work's check
      stops the run the same way)

**Batch C, the Docker host's and Ansible's scripts with no test:**
- [ ] `docker/kea/tools/dnsmasq_to_kea.py`
- [ ] `docker/kea/tools/import_leases.py`
- [ ] `ansible/files/dns-answers.py`
- [ ] `ansible/files/proxmox-backup-metrics.py`
- [ ] `ansible/inventory_plugins/netbox_fallback.py`
- [ ] `ansible/callback_plugins/homelab_progress.py`
- [ ] `docker/netbox/patch_topology_views.py`

## Resuming

Take the first unticked item. Read the script and any test of it, write the tests, run the
sweep and the targeted breaks on that script alone, and tick the item with its counts
("killed N of M; left: the HTTP call") in the commit that adds the tests. A batch is one pull
request; a half-done batch is still valid, since each ticked item stands alone.

## Done when

Every item is ticked: each function outside the out-of-scope list above goes red when its body
is replaced, and each item's targeted breaks go red. Then this file moves to `old-queues/` with
a row in its README.

## Checked, came back negative

- The 57 tracked multi-document YAML files the hygiene scans had skipped scan clean (#562).
- The wider env-var check finds only two values in the tree, both true/false switches in
  Termix's deployment (#567).
- `generate` on the committed tree changes no file (#576).
- `zammad-bridge.py`, in the first sweep's list, has since been removed from the repo.
