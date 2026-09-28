# Homelab work queue

Remaining work on the cluster and on this repo, most important first. Tick
an item off when it's done and move it to **Done** with the date.

How the repo is managed:

- **Helm releases**: captured from the cluster by `capture`; `generate`
  writes their Flux files.
- **`.handwritten` releases** (media, longhorn): the tool never touches them.
- **`.promote` releases**: capture writes the namespace's non-Helm objects
  into `app/`, and Secrets go to git as `${PLACEHOLDER}`s.
- **Secret values**: live only in the gitignored
  `kubernetes/.local/cluster-substitutions-secret.yaml`.

Run everything from the repo root with `python scripts/backup.py all --output .`.

Database backups are handled outside this repo and the cluster config, so
nothing here tracks them.

## In progress

- [ ] **Promote the ingress-nginx and manictime extras.** Markers are in place for
  `ingress-nginx/ingress-nginx-extras` and `manictime/manictime-extras`. A scratch
  run showed no changes to the live objects:
  - **ingress-nginx**: the 3 Cloudflare-tunnel Ingresses (babybuddy,
    babybuddy-mcp, immich) and the 3 custom-header ConfigMaps (`empty-headers`,
    `ingress-nginx-custom-headers`, `nginx-config`).
  - **manictime**: the `manictime-pg` database definition.

  No Secrets are involved (the chart-generated `ingress-nginx-admission`
  certificate is skipped), so there's no local apply step:
  `python scripts/backup.py all --output .`, run the tests, then commit and push.

## Next

- [ ] **Move the databases onto Longhorn.** All 9 run on NFS, which is a known
  reliability risk for Postgres.
  - Create a `longhorn-db` StorageClass with 1 replica and local data (CNPG
    already replicates), and migrate one cluster at a time by rolling its
    instances.
  - This also fixes babybuddy's database, which asks for the `nfs-client`
    StorageClass that no longer exists.
- [ ] **Fix babybuddy's PVC.** `babybuddy/babybuddy-config` still asks for the
  missing `nfs-client` StorageClass. It works while bound, but a rebuild would
  leave it Pending. Migrate the data to a PVC on `nfs-retain-rwo`, or create an
  `nfs-client` StorageClass as an alias.

## Cleanup

- [ ] **Delete the Lost PVCs.** Their volumes are gone, nothing uses them, and all
  of them ask for `nfs-client`:
  - `kubectl delete pvc redis-data-redis-node-0 redis-data-redis-replicas-0 -n default`
  - `kubectl delete pvc redis-data-immich-redis-master-0 -n immich`
- [ ] **Back up `kubernetes/.local/`** somewhere safe. It's the only copy of the
  promoted Secrets' values outside the cluster until there's a keystore.
- [ ] **Fix the bare placeholders** in `kubernetes/.local/variable-substitutions.yaml`:
  write `Child1` and `Child2` as `'${Child1}'` and `'${Child2}'`. Capture warns
  about this every run.
- [ ] **Delete `_to_delete/`** once nothing in it is needed.
- [ ] **Decide on `test/media/jackett`**, the only test-only app. Either add it to
  `test/kustomization.yaml` or delete it.

## Later

- [ ] **Keystore.** Replace the local `cluster-substitutions` Secret with an
  External Secrets operator backed by Vault, 1Password or Bitwarden. The
  `${...}` manifests in git stay as they are; only what creates the
  `cluster-substitutions` Secret in `flux-system` changes.
- [ ] **Longhorn follow-ups.**
  - Set a backup target so volume snapshots leave the nodes.
  - Put the UI behind Authentik. It has no login, so for now use
    `kubectl port-forward -n longhorn-system svc/longhorn-frontend 8080:80`.
- [ ] **Startup order (`dependsOn`).** All generated `ks.yaml` files have an empty
  `dependsOn`, and Flux copes by retrying. Worth adding:
  - apps with a Postgres database depend on `cnpg`;
  - apps with Certificates depend on `cert-manager`.
- [ ] **Namespaces defined twice.** Every app namespace is in both
  `apps/<ns>/namespace.yaml` (`cluster`) and `cluster/namespace/<ns>.yaml`
  (`cluster-resources`), so the two Flux Kustomizations keep relabelling it.
  Untangle this carefully: Flux can delete a namespace while it's being moved.
- [ ] **Test environment runbook.** `suspend: true` on `cluster-test` only freezes
  it. To tear it down: suspend `flux-system`, resume `cluster-test`, delete it,
  then resume `flux-system`. Write that down, or script it.
- [ ] **kubectl version skew.** The local client is 1.36 and the server is 1.33,
  outside the supported ±1. Install a 1.33/1.34 kubectl.

## Done

- 2026-09-28: Finished the media move to shared base plus test overlay. Removed
  the orphaned `production/` tree and the stale test copies.
- 2026-09-28: Fixed the generator:
  - per-app `substituteFrom`;
  - the `cluster-substitutions` Secret name;
  - hand edits (`.gitignore`, `README.md`) are no longer overwritten;
  - Flux-owned `shared/` and `test/` objects are no longer captured.
- 2026-09-28: Made capture Flux-aware:
  - chart source read from the live HelmRelease;
  - Flux's labels stripped;
  - Flux's own objects skipped;
  - hand-written `namespace.yaml` kept;
  - in-repo `--output` allowed.
- 2026-09-28: Installed Longhorn 1.12.1 through Flux (not the default
  StorageClass) and removed the broken v1.7.0 `kubectl` install.
- 2026-09-28: Added `.promote`. Promoted babybuddy, babybuddy-mcp, homeassistant,
  mqtt and noip-duc; capture now collects CloudNativePG `Cluster` definitions.
- 2026-09-28: Promoted Secrets as `${PLACEHOLDER}`s, with their values in the
  local `cluster-substitutions` Secret (babybuddy-mcp, noip-duc).
- 2026-09-28: Promoted `authentik-extras`, `immich-extras` and `emby-extras`,
  including the authentik and immich database definitions and 7 Secrets.
  Capture now skips Authentik-managed outposts and Lost PVCs. No restarts on
  adoption.
