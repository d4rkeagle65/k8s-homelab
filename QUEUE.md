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

## Next

- [ ] **Move the databases off NFS onto local disk.** All 9 run on NFS, which is a
  known reliability risk for Postgres. This also fixes babybuddy's database,
  which asks for the missing `nfs-client` StorageClass.
  - **Longhorn can't do it.** The nodes are Proxmox LXC containers, and a test
    volume on 2026-09-29 faulted: the replica process exits on all three workers.
    Making Longhorn work would need privileged, loosened containers, or VMs.
  - **Plan instead**: local-path-provisioner v0.0.37 with a `local-db`
    StorageClass (`WaitForFirstConsumer`, `Retain`, workers only), on a 128G
    ZFS dataset per worker LXC at `/opt/local-path-provisioner` (done
    2026-09-29, containers 104/105/106, `backup=0`). CNPG's own replication
    covers node loss. Migrate one cluster at a time by rolling its instances;
    CNPG accepts the StorageClass change (checked with a server-side dry-run).
  - **Provisioner live** (2026-09-29, commit `be0e008`). A throwaway volume on
    each worker bound on its own node's dataset and took an fsync'd write as
    UID 26 (CNPG's postgres user), then cleaned up.
  - **whisparr done** (2026-09-29): both instances on `local-db`, primary on
    w03a and replica on w01a. The switchover took 15s, with one failed whisparr
    query during it and none since. The old NFS PVs are Released/Retain as a
    fallback.
  - **Per-database procedure** (the `cnpg` plugin isn't installed):
    1. `flux suspend kustomization <ks>`
    2. Patch `spec.storage.storageClass: local-db` on the Cluster.
    3. Delete the replica's PVC (`--wait=false`), then its pod. Wait for the new
       instance to be streaming with 0 bytes behind.
    4. Switch over by patching the Cluster **status** with `targetPrimary`,
       `targetPrimaryTimestamp` and `phase: Switchover in progress`.
    5. Replace the old primary the same way as step 3.
    6. Set `storageClass: local-db` in git, confirm `flux diff` shows 0
       changes, push, then `flux resume kustomization <ks>`.

    Single-instance databases (immich, manictime) first scale to 2 instances,
    switch over, then go back to 1.
  - **Remaining**: prowlarr, lidarr, radarr, sonarr (media), authentik, babybuddy,
    immich, manictime.
- [ ] **Decide what to do with Longhorn.** It's installed and running, but it
  can't serve volumes on these LXC nodes, and its default disks sit on the 8 GiB
  root filesystems. Either uninstall it through Flux or keep it for a future
  move of the workers to VMs.
- [ ] **Delete babybuddy's old PVC** once you're happy with the move.
  babybuddy has run on `babybuddy-config-v2` (`nfs-retain-rwo`) since
  2026-09-29, with the change in git and Flux resumed. Then run
  `kubectl delete pvc babybuddy-config -n babybuddy`; its PV is `Retain`, so
  the NFS data stays until you remove it.

## Cleanup


- [ ] **`test/media/jackett`** stays as a test-only app for now; not listed in
  `test/kustomization.yaml`.

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
- 2026-09-28: Promoted `ingress-nginx-extras` (the 3 Cloudflare-tunnel Ingresses
  and 3 header ConfigMaps) and `manictime-extras` (the `manictime-pg` database).
  Everything you manage yourself is now in git. Capture skips
  kube-webhook-certgen certificates.
- 2026-09-29: Added a second, isolated ingress-nginx controller
  (`ingress-nginx-isolated`, IngressClass `nginx-isolated`) with its own MetalLB
  IP from `${ISOLATED_INGRESS_IP}` (now `.52`) and `failurePolicy: Ignore` on
  its webhook, and moved ManicTime Server onto it so an isolated VLAN can reach
  only that service.
- 2026-09-29: Cleanup done: deleted all three Lost PVCs, wrapped the
  `Child1`/`Child2` placeholders, backed up `kubernetes/.local/`, and deleted
  the merged branch.
- 2026-09-29: Config-only substitution variables (`replace: false`): rendered
  for Flux, never find-and-replaced. Added `MEDIA_PUID`, `MEDIA_PGID` and
  `SUBDOMAIN_SUFFIX` that way, so the local `cluster-substitutions` ConfigMap now
  matches the live one exactly and is safe to `kubectl apply`. Capture warns
  whenever the live ConfigMap or Secret has keys the local files would drop.
- 2026-09-29: Put the main ingress-nginx controller's
  `externalTrafficPolicy: Local` (previously only set by `kubectl edit`) into
  its Helm values, nested under `controller.service`. The rendered chart changes
  only the Service, which is already `Local` live, so there's no restart.
