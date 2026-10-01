# Homelab work queue

Remaining work on the cluster and on this repo, most important first. Tick
an item off when it's done and move it to **Done** with the date.

How the repo is managed:

- **Helm releases**: captured from the cluster by `capture`; `generate`
  writes their Flux files.
- **`.handwritten` releases** (media, traefik, traefik-isolated,
  external-secrets, smtp-relay, local-path-provisioner): the tool never
  touches them.
- **`.promote` releases**: capture writes the namespace's non-Helm objects
  into `app/`, and Secrets go to git as `${PLACEHOLDER}`s.
- **Secret values**: fields of the `cluster-secrets` item in Vaultwarden,
  synced into the cluster by External Secrets (`kubernetes/secrets/`). List
  or change them with `scripts/vaultwarden-fields.ps1`. Plain settings are in
  `kubernetes/flux/meta/vars/cluster-settings.yaml`.

Run everything from the repo root with `python scripts/backup.py all --output .`.

Database backups are handled outside this repo and the cluster config, so
nothing here tracks them.

## Next

- [ ] **Permanently delete the old PV folders on the Synology** (from about
  2026-10-03, once nothing has turned out to need them). On 2026-09-29 the
  Released PVs of the 9 moved databases, all of `media-test`,
  `babybuddy-config`, and the old `paperless`, `fasten`, `gitlab`, `redis`,
  `emby-config`, `immich-data` and `manictime-server-data` volumes were
  deleted, and their folders were moved into a
  `_deleted-pvs-2026-09-29` folder beside each one. Those holding folders are
  listed in `/root/deleted-pvs-2026-09-29.txt` on the Synology; each PV's
  original path is in the gitignored
  `kubernetes/.local/released-pvs-2026-09-29.csv` (and
  `pv-folders-to-delete-2.txt` for the second batch). As root on the Synology:
  - check: `sort -u /root/deleted-pvs-2026-09-29.txt | while read -r d; do du -sh "$d"; done`
  - delete: `sort -u /root/deleted-pvs-2026-09-29.txt | while read -r d; do rm -rf -- "$d"; done`

  Shell deletes skip Synology's Recycle Bin, so this can't be undone.

## Cleanup


- [ ] **`test/media/jackett`** stays as a test-only app for now; not listed in
  `test/kustomization.yaml`.

## Later

- [ ] **Pi-hole's `local=/<local domain>/` line** (`misc.dnsmasq_lines`, added
  2026-10-01) stops Pi-hole forwarding that domain to the DHCP server, which
  wasn't answering; that fixed Vaultwarden SSO and the `bitwarden-cli`
  restarts. Side effect: hostnames only the DHCP server knows no longer
  resolve. If those are needed, find why the DHCP server's DNS doesn't
  answer, then remove the line.

## Done

- 2026-10-01: Restricted the three Vaultwarden ClusterSecretStores to
  ExternalSecrets in `flux-system`. Confirmed that an undefined `${VARIABLE}`
  already fails its Kustomization (kustomize-controller's strict substitution,
  on by default since v1.9), and corrected the docs that said it becomes empty.
- 2026-10-01: Replaced ingress-nginx with Traefik, in six batches (the plan,
  `QUEUE-traefik.md`, was removed once done: `git log -- QUEUE-traefik.md`).
  The SMTP relay test is in its `deployment.yaml`, and the Pi-hole DNS layout
  in Traefik's `helmrelease.yaml`. The main Traefik holds the main ingress address (apps, the
  SMTP relay on 8025, the Cloudflare tunnel routes); `traefik-isolated` holds
  the isolated address (ManicTime). Every app uses Traefik's own Ingress
  classes (`traefik`, `traefik-isolated`), with the `redirect-https` and
  `authentik` Middlewares in place of nginx annotations. Both ingress-nginx
  releases, their IngressClasses and the `ingress-nginx` HelmRepository are
  gone.
- 2026-10-01: Generated files have no date in their header, so a
  regenerated file only shows its real change. Promoted manifests keep the
  `kubectl rollout restart` annotation git already has, and Flux ignores
  that field (`spec.ignore`, kustomize-controller 1.9+), so a restart never
  shows up as a change.
- 2026-10-01: `scripts/vaultwarden-fields.ps1` lists and sets the
  `cluster-secrets` Vaultwarden fields through the bitwarden-cli pod
  (base64-encoding values used in Secrets).
- 2026-10-01: Removed the immich release's unused `power-tools` values (the
  chart has no such component; Power Tools runs from `immich-extras`).

- 2026-09-30: Test environment runbook: "Bring the test environment up, and take
  it down" in `scripts/README.md`.
- 2026-09-30: kubectl version skew fixed: the local client is now v1.34.12
  (server 1.33), in `%LOCALAPPDATA%\kubectl`, first on the user PATH.
- 2026-09-30: Keystore. Substitution variables now come from Vaultwarden:
  External Secrets reads the `cluster-secrets` item through a Bitwarden CLI pod
  (held at CLI 2026.8.0; 2026.9.0 fails against Vaultwarden) into the
  `cluster-secrets` Secret, and plain settings are in the `cluster-settings`
  ConfigMap in git. capture reads both from the cluster; new private values go
  to `kubernetes/.local/vaultwarden-pending.yaml`. The old `cluster-substitutions`
  ConfigMap/Secret and local rendered files are retired.
- 2026-09-30: smtp-oauth-relay: SMTP to Microsoft 365 over Graph, on the main
  ingress address, port 8025. Vaultwarden sends mail through it.
- 2026-09-29: Namespaces defined once. generate marks every Namespace
  `prune: disabled` (deleted by hand now), and a namespace with an app folder
  is only in `apps/<ns>/namespace.yaml`; `cluster/namespace/` holds the rest.
  Removed the unused `redis` and `tandoor` namespaces.
- 2026-09-29: Uninstalled Longhorn through Flux (it can't serve volumes on
  these LXC nodes), then removed its `longhorn-system` namespace.
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
