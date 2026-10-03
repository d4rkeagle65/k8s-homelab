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

- [ ] **New Docker host: move Vaultwarden to it, then retire NPM and the old
  Vaultwarden host.** The new host is a Debian 13 VM on the Proxmox cluster;
  the old one runs Debian 11, which has had no security updates since
  2026-08-31. The VM is built (Docker CE, guest agent, automatic security
  updates) and dockhand already runs there (see Done).
  1. **Vaultwarden:**
     - move its data, with the same `ROCKET_TLS`/443 setup;
     - set up `scripts/vaultwarden-cert-sync.sh` there (config dir and daily
       cron), and remove it from the old host;
     - repoint the hand-made `vaultwarden-direct.<domain>` Pi-hole record at
       the VM.

     The Traefik route and the `bitwarden-cli` pod both use that name, so
     nothing in git changes. Afterwards, check that `cluster-secrets` still
     syncs.
  2. Retire NPM and the old Vaultwarden host.
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

- [ ] **DNS: drop the second DNS server DHCP hands out** (or make it a second
  Pi-hole). It doesn't know local names, so a PC that asks it caches "no such
  host" for names like the cluster API endpoint. That causes intermittent
  `kubectl` lookup failures until the cache clears.
- [ ] **immich's Redis eviction policy.** immich's job queue (BullMQ) logs that
  Redis uses `volatile-lru` and should use `noeviction`; under memory
  pressure, queued jobs could be dropped. Check which Redis immich uses
  before changing it, since a shared Redis affects its other users too.
- [ ] **Keep immich and manictime up through a node loss** (optional). Today
  their single database copy sits on one worker's disk.
  - Set `instances: 2` on `immich-postgres` and `manictime-pg`, for automatic
    failover in under a minute.
  - Shorten the app pods' `node.kubernetes.io/unreachable`/`not-ready`
    tolerations from the default 5 minutes to about 30 seconds.
  - Cost: double the database disk on another worker.
- [ ] **Proxmox host cleanup.**
  - Run `apt autoremove` on each host to clear the Proxmox 8 leftovers.
  - Once kernel 7.0 has run cleanly for a couple of weeks, remove
    `6.14.11-4` (keep `6.14.11-9` as the fallback): `/boot` is only 456 MB.
  - Don't run `zpool upgrade` unless a new ZFS feature is needed.

- [ ] **SSH to the k8s nodes only from the management network.** Each node
  now has a management-network interface (`eth1`), so sshd can listen there
  only, or a firewall can limit port 22 to it. Keep Calico on `eth0` (see
  CLAUDE.md).
- [ ] **MetalLB: announce only on `eth0`.** The L2Advertisement
  (`cluster/l2advertisement.metallb.io/`) has no `interfaces` list, so since
  the nodes got `eth1` MetalLB may also answer for service addresses on the
  management network. Add `interfaces: [eth0]`; check first how `generate`
  treats the file (change the live object and recapture, or hand-edit).
- [ ] **Pi-hole's `local=/<local domain>/` line** (`misc.dnsmasq_lines`, added
  2026-10-01) stops Pi-hole forwarding that domain to the DHCP server, which
  wasn't answering; that fixed Vaultwarden SSO and the `bitwarden-cli`
  restarts. Side effect: hostnames only the DHCP server knows no longer
  resolve. If those are needed, find why the DHCP server's DNS doesn't
  answer, then remove the line.

## Done

- 2026-10-03: dockhand moved to the new Docker host, served through Traefik
  at `dockhand.<domain>` (`apps/external-services/dockhand.yaml`). It
  listens only on the host's management-network address, and
  `DOCKHAND_ADDRESS` is that address's hand-made Pi-hole name. Its NPM CNAME
  was deleted.
  - The host is on both networks, with source-based routing for its
    management address. Replies from Docker containers come from the
    container's own address, so the host also marks connections that arrive
    on the management interface and routes their replies back out of it.

- 2026-10-03: The k8s nodes got a second interface (`eth1`) on the management
  network, with no gateway, so pods can reach management-only hosts. Calico
  re-detects node addresses every minute with `firstFound`, picked `eth1`, and
  moved all pod-to-pod traffic onto the management network. It's now pinned
  to `eth0` (see CLAUDE.md); the change restarted Calico one node at a time
  with no failures.

- 2026-10-02: Upgraded the four Proxmox hosts from VE 9.0.15 to 9.2.21 (kernel
  6.14 to 7.0.14-20, ZFS 2.4.4), rolling one host at a time.
  - **For each k8s host:** etcd leadership and every database primary on its
    worker were moved off first, by switchover with 0 lag; then the control
    plane and worker were drained (database copies stay), the host upgraded
    and rebooted, and the nodes uncordoned.
  - **Pi-hole** was migrated between hosts so DNS never went down.
  - **Downtime:** only immich and manictime (single database copy), during
    the last host's reboot.
  - **Fixes on the way:**
    - `/boot` (456 MB) ran out of space on the first host, so remove the
      Proxmox 8 kernels before upgrading;
    - the paid `pve-enterprise` repository was disabled on the two hosts
      that had it;
    - one host's earlier half-done upgrade (`apt upgrade` instead of
      `apt full-upgrade`) was completed.

- 2026-10-01: mealie, pihole and vaultwarden moved from Nginx Proxy Manager
  to Traefik (`apps/external-services/`). They're LAN and VPN only, with
  cert-manager certificates and Pi-hole records from ExternalDNS.
  - **Vaultwarden** serves HTTPS itself on 443 with a cert-manager
    certificate, which `scripts/vaultwarden-cert-sync.sh` copies to its host
    daily (cron). Traefik reaches it at `vaultwarden-direct.<domain>`.
  - **The `bitwarden-cli` pod** also uses `vaultwarden-direct.<domain>` (a
    hand-made Pi-hole record), so a rebuilt cluster can always fetch its
    secrets without Traefik.

- 2026-10-01: ExternalDNS writes a Pi-hole address record for every `traefik`
  and `traefik-isolated` Ingress hostname (ManicTime at `ISOLATED_INGRESS_IP`,
  the rest at `INGRESS_IP`), plus `smtp-relay` from an annotation on Traefik's
  Service. It replaced the hand-made app-name CNAMEs to the old `k8snginx`
  targets. It's upsert-only, so other hand-made records stay,
  and a removed hostname's record is deleted by hand.

- 2026-10-01: Closed a login bypass in babybuddy. It trusts the
  `X-Authentik-Username` header on every path: its attempt to skip `/api`
  checks `request.path.startswith("api/")`, which never matches. And `/api`
  had no Authentik check, even through the Cloudflare tunnel, so anyone could
  send the header themselves.
  - Traefik now removes incoming `X-authentik-*` headers on `web` and
    `websecure`.
  - babybuddy's API is private: `/api` is served on `websecure` to private
    addresses only, so Home Assistant and the LAN keep it. Through the tunnel,
    `/api` falls to the main route behind Authentik, so the web UI's own calls
    still work.
  - A NetworkPolicy lets only Traefik and babybuddy-mcp reach babybuddy's pods.

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
