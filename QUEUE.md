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

- [ ] **Delete the old NFS database volumes** once you're comfortable. Each of
  the 9 databases left its pre-move NFS PVs `Released` with `Retain`, as a
  fallback. List them with
  `kubectl get pv | Select-String Released`. Delete the PV objects, then the
  folders on the Synology.
- [ ] **Decide what to do with Longhorn.** It's installed and running, but it
  can't serve volumes on these LXC nodes, and its default disks sit on the 8 GiB
  root filesystems. Either uninstall it through Flux or keep it for a future
  move of the workers to VMs.

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

- 2026-09-29: Resumed the 6 Kustomizations suspended for the database move.
- 2026-09-29: Deleted babybuddy's old `babybuddy-config` PVC; its PV is
  `Released` (`Retain`) until the old-volume cleanup.
- 2026-09-29: Startup order. generate fills in `dependsOn` on each generated
  `ks.yaml` from the operator APIs its manifests use (`cnpg`, `cert-manager`),
  and sets `wait: true` on the operators. The hand-written *arr apps depend
  on `cnpg` by hand.
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
- 2026-09-29: Moved all 9 CloudNativePG databases off NFS onto local disk.
  - **Why not Longhorn:** the nodes are Proxmox LXC containers on ZFS. A test
    volume faulted, because ZFS has no file-extent support and the containers
    have no block-device access.
  - **Storage:** local-path-provisioner v0.0.37 (`be0e008`) provides the
    `local-db` StorageClass (`Retain`, created on the pod's node, workers only),
    on a 128G ZFS dataset per worker LXC at `/opt/local-path-provisioner`.
  - **The move:** instances were rolled one at a time with
    `scripts/migrate-cnpg-to-local-db.ps1`, with one switchover of a few
    seconds each. Commits: whisparr `3e6567c`, lidarr `4413dce`, the rest `774dacf`.
  - **Also fixed:** babybuddy's database no longer asks for the missing
    `nfs-client` class.
