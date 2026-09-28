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

## In progress

- [ ] **Promote the leftover pieces of Helm namespaces.** Markers are in place for
  `authentik/authentik-extras`, `immich/immich-extras` and `emby/emby-extras`.
  A scratch run against the live cluster showed no changes beyond Flux's own
  labels. What each one picks up:
  - **authentik**: the Cloudflare Ingress, the `authentik-ingress-nginx` Service,
    the LDAP Certificate, the `authentik-postgres` database, and 3 Secrets
    (`authentik-secret-bootstrap`, `authentik-secret-key`, `redis-password`).
    Authentik's own outposts are skipped.
  - **immich**: immich-power-tools (Deployment, Service, Ingress), the
    `immich-data` PVC, the `immich-postgres` database, and 4 Secrets
    (`immich-postgres-superuser`, `immich-power-tools-apikey`, `immich-redis-url`,
    `redis-password`).
  - **emby**: the Ingress and the `emby-media` PVC.

  Steps:
  1. `python scripts/backup.py capture --output .` seeds the 7 Secrets' values
     locally; their manifests are held back.
  2. `kubectl apply -f kubernetes/.local/cluster-substitutions-secret.yaml`
  3. `python scripts/backup.py all --output .`
  4. Check that every `secret-*.yaml` holds only `${...}` values, run
     `python -m pytest scripts/tests -q`, then commit and push.

## Next

- [ ] **Back up the databases.** None of the 9 CloudNativePG clusters has a backup
  configured: authentik, immich, manictime, babybuddy, and lidarr, prowlarr,
  radarr, sonarr, whisparr in media. Their data is protected only by the
  Synology's NFS snapshots.
  - **Decision needed**: the backup target. Options are an S3-compatible bucket
    (MinIO on the Synology, Backblaze B2, Wasabi) or an NFS share.
  - **Then**: turn on continuous WAL archiving, add a nightly `ScheduledBackup`
    per cluster, and do one test restore.
- [ ] **Move the databases onto Longhorn.** All 9 run on NFS, which is a known
  reliability risk for Postgres.
  - Create a `longhorn-db` StorageClass with 1 replica and local data (CNPG
    already replicates), and migrate one cluster at a time by rolling its
    instances.
  - This also fixes babybuddy's database, which asks for the `nfs-client`
    StorageClass that no longer exists.
  - Do this after backups are in place.
- [ ] **Promote the ingress-nginx extras.** The three Cloudflare-tunnel Ingresses
  (babybuddy, babybuddy-mcp, immich) and the custom-header ConfigMaps
  (`empty-headers`, `ingress-nginx-custom-headers`, `nginx-config`) are only on
  the cluster. They are external access, so this is the next promotion:
  `ingress-nginx/ingress-nginx-extras/.promote`.
- [ ] **Promote the manictime extras.** The `manictime-pg` database definition is
  only on the cluster: `manictime/manictime-extras/.promote`.
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
