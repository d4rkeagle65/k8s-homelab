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

Nothing right now; pick from Later.

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
- [ ] **Kea phase 3:** a second Kea on another host as a hot-standby partner
  (the firewall's relay can point at both), and lease names into DNS.
- [ ] **One Proxmox host carries both Pi-hole and the Docker VM**, so its
  reboot takes DNS, DHCP, the VPN and Vaultwarden down
  together. A second Pi-hole and a Kea HA partner on other hosts would fix
  that.
- [ ] **The Vaultwarden secret stores didn't recover by themselves** after
  Vaultwarden's move on 2026-10-03, despite `refreshInterval: 1m`: they
  stayed `InvalidProviderConfig` until the recheck annotation
  (`kubectl annotate clustersecretstore <name> homelab.local/revalidate=<time> --overwrite`).
  Find out whether External Secrets rechecks a store that's already invalid,
  or only valid ones, and make recovery automatic.
- [ ] **Ansible for the hosts outside the cluster**, so their hand-made setup
  can be rebuilt from git instead of from notes:
  - **Docker VM:** netplan (its three network legs, routing tables and rule
    priorities), the nft scripts and their systemd units (management return
    path, DMZ marks, the `DOCKER-USER` rules for Tailscale), sysctls, Docker,
    Dockhand's own compose file and the certificate-sync cron.
  - **Proxmox hosts:** repositories, packages, kernel cleanup, and the host
    settings the k8s LXCs depend on.
  - **Pi-hole:** its `pihole-FTL --config` settings (the `local=` lines,
    HTTPS filtering, no conditional forwarding, `bogusPriv`) and the
    hand-made local records such as `vaultwarden-direct`.

  The inventory and every address stay out of this public repo (a
  gitignored inventory, or values from Vaultwarden).
- [ ] **Terraform (or OpenTofu) for what's created through APIs:**
  - **Proxmox:** the VMs and LXCs (k8s nodes, the Docker VM, Pi-hole), with
    their disks and network interfaces.
  - **Cloudflare:** DNS records and the tunnel's public hostnames.
  - **The *arr settings:** download clients, root folders, auth and
    Prowlarr's app links, through the devopsarr providers. This is the
    alternative to a small in-cluster Job calling the apps' APIs, which
    needs no state file.
  - **Dockhand:** its Git credentials, the Git stacks it deploys from
    `docker/<stack>/` and their variables, plus its own settings and login,
    through the community `kalebharrison/dockhand` provider (unofficial,
    with known gaps, so a Dockhand update can break it). Dockhand can't set
    itself up: Ansible installs it on the VM, then Terraform configures it.

  Before starting: decide where the state file lives (it holds secrets,
  including the stacks' secret variables, so not this repo) and what runs it
  (by hand, CI, or tofu-controller under Flux). Import the existing resources
  first, so nothing gets recreated.

## Done

- 2026-10-03: The cleanup after the moves:
  - **The retired hosts are deleted:** the NPM and mealie host, the old
    dockhand, the old Vaultwarden host, the dnsmasq DHCP VM and the old
    Tailscale container, with their Pi-hole records. The AWS instance at the
    far end of the old WireGuard tunnel is gone too.
  - **The old PV folders on the Synology are deleted** (the Released PVs of
    2026-09-29). One of them, the old `immich-data`, turned out to hold
    immich's only copy of the photos: immich's volumes were recreated on
    2026-05-17, and the photos were never copied into the new one. They're
    being synced back from another copy. Before deleting any old volume,
    compare its contents with its replacement's.
  - **The Docker host's `/opt` disk** has Discard and SSD emulation, and
    `/opt.old` is deleted.

- 2026-10-03: Kea phase 2. The `kea` stack gained Stork (Kea's web UI, with
  its agent sharing Kea's container) and a PostgreSQL database, and all the
  reservations moved from the site file into that database, so Stork and the
  API change them live. Stork is at `stork.<domain>` through Traefik
  (`external-services`, private networks only) and logs in through
  Authentik, from a blueprint like dockhand's; only `authentik Admins` may
  log in, as super-admins. Stork's own admin login stays as the fallback.
  - **Stork comes from ISC's development series** (2.5.1): OIDC login isn't
    in a stable release before 2.6. Stork migrates its database one way, so
    go back to the stable repository only at 2.6 or later.
  - **A reservation left in the site file overrides** the database's for
    the same client, so the file keeps none.

- 2026-10-03: Pi-hole answers every local domain itself (`local=` lines in
  `misc.dnsmasq_lines` for all five), with conditional forwarding removed
  and private-range reverse lookups kept off the internet (`bogusPriv`).
  Everything had been forwarded to the DHCP servers, which never answered
  DNS: any name or reverse lookup Pi-hole couldn't answer itself hung for
  10 to 20 s. That broke Vaultwarden SSO and the `bitwarden-cli` pod, and
  made Traefik take 10 s per new connection to dockhand. DHCP clients'
  own names don't resolve (they never did); Kea phase 3 would add them.

- 2026-10-03: DHCP moved from the dnsmasq VM to Kea 3.2 on the Docker host
  (`docker/kea/`), every VLAN relayed to it by the firewall's DHCP relay.
  dnsmasq's config and current leases were converted and imported first,
  so every device kept its address; each VLAN then moved separately (stop
  dnsmasq on it, add it to the relay, test a renewal). The management pool
  moved off addresses with fixed devices on them: dnsmasq pings before
  offering an address, Kea doesn't.

- 2026-10-03: Tailscale moved onto the Docker host (`docker/tailscale/`),
  replacing the old container in the DMZ: the same subnet routes and exit
  node, no source NAT, accepting the travel router's route. Tagged and
  registered with an OAuth client, so no key expires. See its README; the
  lessons:
  - **The host's DMZ leg carries everything the node forwards**, so the
    firewall still sees both directions; a host sitting on the main and
    management networks would otherwise route around it.
  - **The two nodes can't run at once**: with `--accept-routes`, the old
    node's routes for the host's own subnets would take over its routing.
    The deploy was the cutover.
  - **Docker's FORWARD DROP** blocked everything forwarded until two
    `DOCKER-USER` rules (`tailscale0` to and from the DMZ leg) went in.
  - **A firewall connection object with an explicit next hop** overrode the
    route to the travel router; it now points at the host too.

- 2026-10-03: mealie moved into the cluster (`apps/mealie/`, hand-written),
  from SQLite on a Docker host to a CloudNativePG database with two copies,
  with its files on NFS. Moved with mealie's own backup and restore; logins
  still go through Authentik, at the same address. Two snags:
  - **Restore needs `SET session_replication_role`**, which only a superuser
    may set; the database's `app` user was granted just that parameter
    (also in `postgres.yaml`, for a rebuilt database).
  - **Restart mealie after a restore**: the restore replaces its signing
    key file, and until a restart every login loops back to Authentik.

  NPM, the old dockhand and the three old Docker hosts are powered off.

- 2026-10-03: Vaultwarden moved to the new Docker host (a Debian 13 VM on the
  Proxmox cluster). Dockhand deploys it from `docker/vaultwarden/` as a Git
  stack, with credentials as Dockhand secret variables.
  - **Storage:** `/opt` on the VM is its own virtual disk, holding the apps'
    data (Vaultwarden's vault, dockhand's database), and Docker won't start
    without it mounted.
  - **Certificate:** `scripts/vaultwarden-cert-sync.sh` runs there daily from
    cron.
  - **Cutover:** about 20 minutes of downtime. The old instance was stopped,
    its data volume copied, then `vaultwarden-direct.<domain>` repointed in
    Pi-hole. The data was checked (same instance key and database) before
    the switch.
  - **Also fixed:** `SSO_SCOPES` carried literal quotes in the old compose
    file, so Vaultwarden never got refresh tokens from Authentik, and token
    debug logging is off.

- 2026-10-03: Pi-hole drops HTTPS-type DNS records (`filter-rr=HTTPS` in
  `misc.dnsmasq_lines`). It used to pass Cloudflare's record through for the
  names that are both public (Cloudflare tunnel) and local (`auth`, `bb`,
  `bb-mcp`), so browsers on the LAN tried HTTP/3 and Cloudflare's Encrypted
  Client Hello against local Traefik and failed (`ERR_QUIC_PROTOCOL_ERROR`).
  The cost: no Encrypted Client Hello for any site on the LAN.

- 2026-10-03: dockhand logs in through Authentik (OIDC), defined as an
  Authentik blueprint in git: the `authentik-blueprints` ConfigMap in
  `authentik-extras` (see CLAUDE.md). Only `authentik Admins` may use it,
  since dockhand's free edition makes every SSO user an admin. A provider
  made by a blueprint starts with no grant types, so the blueprint sets
  them. dockhand's local admin login stays as the fallback.

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
