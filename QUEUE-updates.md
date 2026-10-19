# Version updates that need more than a version bump

**The ask (2026-10-11):** check every pod for newer versions. The patch and minor updates went
in as two commits (the platform, then the apps and the PostgreSQL minor version). What's left
here changes behaviour, a data format or the cluster itself, so each is its own change, checked
live before the next.

## How a version was checked

Once Renovate runs (`services/renovate/`), its Dependency Dashboard and Grafana's Homelab
updates list what git pins; the items below arrive there as pull requests, held for these
checks. Images on floating tags aren't pinned in git, so Renovate skips them: for those, the
check below still applies.

Each running image (`kubectl get pods -A`, containers and init containers) against its
registry's tags: the newest tag of the same shape (prefix, number of parts, suffix such as
`-alpine`), pre-releases skipped, reported within the same major version and overall. A floating
tag (`latest`, `hardcover`, a digest pin) is compared by digest: the registry's current digest
for the tag against the pods' `imageID`. Each HelmRelease's chart against its HelmRepository's
`index.yaml`. Then each release's notes between the two versions, read for breaking changes,
not just grepped: MetalLB and Calico keep theirs in the repo (`website/content/release-notes/`,
`release-notes/`), not in GitHub releases.

## Rules

- **One item per commit, verified live before the next.** "Applied" means the new version is
  running and the app answers: the HelmRelease's `status.history`, the pod's image, and the
  app's own health (a login, a page, a query).
- **A captured release changes through `release.yaml`'s `chartVersion` and `app/values.yaml`**,
  then `generate` (`scripts/README.md`, "Upgrade a captured release's chart").
- **Read every release's notes between the two versions**, not only the newest.

## Checklist

- [x] **Mealie v3.20.1 -> v3.28.0.** v3.21's `email_verified` requirement: the `mealie`
      Authentik blueprint swaps the provider's email mapping for one that says true. v3.26's
      limit on what Mealie fetches touches nothing here (no webhooks or recipe actions; recipe
      imports are from the internet), so no `HTTP_ALLOW_LIST`. No setting Mealie reads was
      renamed between the two.
- [x] **MetalLB 0.15.2 -> 0.16.1.** L2 only, so both BGP backends off (frr-k8s, the new
      default, and FRR mode); the speakers lose their three FRR containers. The chart's
      ServiceMonitors follow the metrics to HTTPS on 9120 themselves.
- [x] **Calico v3.32.2 -> v3.33.0** (operator v1.44.0). The pool is VXLAN cross-subnet, so the
      IPIP route change doesn't apply; the CNI config's `cniVersion 1.0.0` needs CRI-O 1.24+
      (1.36.7 here); the chart carries no CRDs, the operator applies them (`-manage-crds`).
      Checked with a pod-network mesh test (a pod on each node calling every node's Alloy
      pod, cluster DNS and a ClusterIP Service) before and after.
- [x] **Authentik 2026.5 -> 2026.8.3** (chart 2026.5.6 -> 2026.8.3; one feature release). The
      image is pinned to the chart's version instead of the floating `2026.5`. 2026.8 reads
      forwarded headers only from `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS`; every pod that
      proxies to it (Traefik, the tunnel connector, the outposts) is inside the default ranges,
      so it stays unset. `AUTHENTIK_WEB__BASE_URL` seeds the Base URL setting 2026.11 requires.
      Both outposts run in the cluster and follow the server's version. The chart's defaults
      didn't change. Checked with a backup first, then one forward-auth and one OIDC login.
- [x] **app-template 3.7.3 -> 5.3.0** (2026-10-15): every release, each Deployment recreated
      for the changed selector label; `old-queues/QUEUE-app-template.md`.
- [x] **PostgreSQL 18 on Debian trixie, by a new cluster per database** (the owner,
      2026-10-15; done 2026-10-18, `old-queues/QUEUE-postgres18.md`). Every cluster but
      Immich's ran the plain `postgresql:15`/`:16`/`:17.11` tags, which are Debian 11
      (bullseye) `system` images: those tags are deprecated and no
      longer built, so they get no patch releases, and Debian 11's LTS ended 2026-08.
      CloudNativePG's in-place major upgrade can't change the Debian release, so each
      database moves to a new `<app>-postgres` cluster on `18.x-standard-trixie`: created
      empty with the same databases and owner, the data streamed in with `pg_dump | pg_restore`
      (`kubectl exec` as `postgres` on each side, no passwords) while the app is stopped,
      every table's rows compared, then the app pointed at the new cluster's `-rw` Service and
      `-app` Secret, and the old cluster removed. Check where the app really reads its
      connection first (ManicTime's is a file on its volume). A base backup and a restore test
      first (`old-queues/QUEUE-restore-tests.md`). ManicTime publishes no PostgreSQL range
      (its own Docker guide uses 15), so its first start on 18 is the test.
  - [x] ManicTime (2026-10-15): on `manictime-postgres` (18.6, trixie), every table's rows
        and every object identical to the old cluster before the switch; about five minutes
        down. **Its database connection lives in `ManicTimeServerSettings.json` on the
        `manictime-server-data` volume**, copied from the `manictime-server-settings` Secret
        only when missing, so changing the release's `settings.database` alone changes
        nothing: replace the file from the Secret too (the old one is kept beside it as
        `.pre-pg18`).
  - [x] `manictime-pg` retired 2026-10-17 (`old-queues/QUEUE-postgres18.md`)
  - [x] Whisparr (2026-10-15): on `whisparr-db` (18.6, trixie); rows, objects and sequences
        identical before the switch; under two minutes down. New clusters whose
        `<app>-postgres` name is taken are `<app>-db`.
  - [x] `whisparr-postgres` retired 2026-10-17 (`old-queues/QUEUE-postgres18.md`)
  - [x] The other eleven (2026-10-15, each old cluster retired 2026-10-17):
        [old-queues/QUEUE-postgres18.md](old-queues/QUEUE-postgres18.md).
- [ ] **Immich's PostgreSQL, when an Immich release calls for it.** `immich-postgres` runs
      `cloudnative-vectorchord:16-1.1.1` (Debian 12), which is supported, so it isn't due: it
      moves when an Immich release's notes ask for a newer PostgreSQL or VectorChord. Then as
      the others did (`old-queues/QUEUE-postgres18.md`'s rules: a new cluster beside it, the
      data copied with the app stopped, rows, objects and sequences compared, the old one
      retired a day later), on a VectorChord image of the new major, since Immich needs it.
- [ ] **Kubernetes 1.36.5 -> 1.37.1** (with CoreDNS and etcd): by
      `ansible/talos-k8s-upgrade.yml`, as `QUEUE.md`'s 1.37 entry says. Held (2026-10-15)
      until these list 1.37 as supported: Flux (2.9.6 says 1.34-1.36; its unreleased main
      adds 1.37), cert-manager (1.21.2 says 1.33-1.36; 1.22 due about 2026-11), External
      Secrets (2.12.0 says 1.36), CloudNativePG (1.30.1 supports 1.34-1.36, "tested, but not
      supported: 1.37"). Already fine: Talos 1.14 (1.37.1 is its default), Calico 3.33 (tests
      1.37), metrics-server, ExternalDNS and local-path (open ranges); the rest state none. No
      removed API is in use (only core/v1 Endpoints, deprecated with no removal release), and
      1.37's notes touch nothing here (no SELinux, IPVS mode or cAdvisor flags). Renovate's
      pull request for it is ready to merge once they do. Rechecked 2026-10-19: Flux,
      cert-manager and CloudNativePG have no newer release; External Secrets 2.13.0
      (2026-10-16) is unread.
- [ ] **Renovate never offers an image from outside Docker Hub** (found 2026-10-19).
      `renovate.json`'s `minimumReleaseAge` meets Renovate 42's default
      `minimumReleaseAgeBehaviour: timestamp-required`, which holds a release with no timestamp
      for good, and the docker datasource gets timestamps only from Docker Hub. So every
      ghcr.io, lscr.io, quay.io, public.ecr.aws and Cloudsmith image stays under the dashboard's
      "Pending Status Checks" (Immich 3.3.3, released 2026-10-14, still there 2026-10-19), and a
      group of a chart and its image opens with the chart alone: Authentik's chart 2026.8.4
      without `ghcr.io/goauthentik/server` 2026.8.4, the mismatch its group rule is there to
      stop. The fix is the owner's call, since it drops the wait for those images: a
      packageRule with `matchDatasources: ["docker"]` and `minimumReleaseAgeBehaviour:
      "timestamp-optional"` (Docker Hub releases keep their timestamps, so their wait stays).
      Then Authentik's chart pull request gains its image and can merge.
- [ ] **Renovate offers OpenTofu for the claude-code image** (found 2026-10-19).
      `docker/claude-code/Dockerfile`'s `TOFU_VERSION` carries a `# renovate:` comment, so
      Renovate opened 1.13.1; but `tofu/*/versions.tf` requires exactly `1.11.0` (Semaphore's),
      so that image's `tofu` would refuse both configurations. A packageRule turning
      `opentofu/opentofu` off, saying it moves by hand with Semaphore's image and
      `required_version`, then close the 1.13.1 pull request.
- [x] **Images on floating tags pinned** to the version each was running (read from the
      image's version label, or matched by digest): the linuxserver apps to their full
      `<version>-ls<n>` tags, Jellyseerr 2.7.3, Tdarr 3.00.00, noip-duc 3.3.0. `renovate.json`
      gives the linuxserver tags (and qBittorrent's `_v<libtorrent>`) a versioning regex, so
      Renovate offers them as updates. Tdarr's server and node share a pod, so they always
      restart together; the node's `3.00.00` tag is a newer build of the same version than
      the one running, and it pulls that on its next restart.
- [x] **Jellyseerr -> Seerr v3.5.0** (`ghcr.io/seerr-team/seerr`, 2026-10-15). Same config
      path and port; Seerr migrates Jellyseerr's settings and database on its first start, one
      way, so a tar of the config came first; it runs as UID 1000, not root, so the config
      claim was chowned to it (the NAS export doesn't squash root). Renamed to `seerr`
      throughout (release, Flux Kustomization, claim `seerr-config`, hostname, Homepage,
      Organizr, `SEERR_API_KEY`); the config was copied to the new claim with Seerr stopped.

- [x] **Renovate watches every version it can** (the owner, 2026-10-15; the 20:34 run detected
      every gap below, 431 dependencies from 411). Inventoried
      2026-10-15 against the Dependency Dashboard's detected list (issue #3; the run logs only
      totals at `LOG_LEVEL=info`): images in HelmReleases' inline values were already read. The
      gaps, closed the same day: a chart value with a tag but no `repository` (Immich's server,
      ManicTime's, Obsidian's CouchDB: the chart's default repository now named beside it),
      democratic-csi's `registry`-named image and the Dockerfile `ARG`s (kubectl, Flux, Helm,
      OpenTofu in `docker/claude-code/`, Kea and Stork in `docker/kea/`), now read by the
      custom manager for a `# renovate: datasource=... depName=...` comment on the line above.
      Kea and Stork minors wait for approval (their package sources are per minor). Stable
      releases only (`ignoreUnstable`, a rule refusing tags naming a pre-release), and images
      pin full versions: a tag pinned to a minor (`valkey/valkey:9.0-alpine`) let Renovate
      offer `9.2-alpine` while it was `9.2.0-rc1`. Kea's minors are ISC's
      development series when odd (3.3): approve only an even one. Left, each on
      purpose: `homelab/claude-code` and `homelab/kea-tools`, built on the host from their
      Dockerfiles (`pull_policy: build`, tag `local`; Renovate skips `homelab/**`); `debian:trixie-slim`
      and `buildpack-deps:trixie-curl`, Debian codename tags with no version to compare;
      BabyBuddy MCP's `>=` requirements in its ConfigMap (below); OpenTofu's
      `required_version` (matches Semaphore's, a hand update), `values-all.yaml` and the
      other `ignorePaths`.

## Left as they are, and why

- `busybox:1.36` in ManicTime's `seed-settings`: the chart's default (`d4rkeagle65`), changed in
  the chart, not here.
- `python:3.12-slim` in `babybuddy-mcp`: it installs its requirements at start; move with a
  test of the server.
- `node:22-bookworm-slim` in `bitwarden-cli`: Node 22 is a supported LTS line until 2027-04.
- `curlimages/curl:latest` and `busybox:latest` in Obsidian's CouchDB: the chart's defaults for
  its setup job and init container, set in the chart, not here.
- Loki's `k8s-sidecar:2.5.0` and Redis `8.2.4`: what the newest charts ship.

## Resuming

Rerun the check above; the first unticked box is next.

## Done when

Every box is ticked or moved to `QUEUE.md` with a reason, and this file moves to `old-queues/`.
