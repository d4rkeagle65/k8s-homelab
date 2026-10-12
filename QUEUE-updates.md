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
- [ ] **app-template 3.7.3 -> 5.3.0** (15 handwritten releases: the media apps, Grimmory,
      Homepage, mesh-mini, n8n, Organizr). Two major versions, each changing the values
      format. Migrate one release, verify it, then the rest by the same pattern.
- [ ] **PostgreSQL major versions:** `manictime-pg` on 15 and `whisparr-postgres` on 16, to 18; Immich's on vectorchord `16-1.1.1` (an extension, so Immich's own upgrade notes
      decide when). CloudNativePG upgrades a major version offline in place when `imageName`
      changes major. Take a base backup and run a restore test first
      (`old-queues/QUEUE-restore-tests.md`).
- [ ] **Kubernetes 1.36.5 -> 1.37.1** (with CoreDNS and etcd). Either a kubeadm upgrade
      (`k8s-upgrade.yml`) or as part of the Talos move (`QUEUE-talos.md`): decide there.
- [ ] **Images on floating tags** (Lidarr, Prowlarr, qBittorrent, Radarr, Sonarr: `latest`;
      Tdarr and its node: `latest`) pick up a new build only on a restart, so their versions
      aren't in git. Pin each to a version tag. Tdarr's server and node must run the same
      version: on 2026-10-11 the node's tag had a newer build than the running one while the
      server's didn't, so restart them together, never one alone.

## Left as they are, and why

- `busybox:1.36` in ManicTime's `seed-settings`: the chart's default (`d4rkeagle65`), changed in
  the chart, not here.
- `python:3.12-slim` in `babybuddy-mcp`: it installs its requirements at start; move with a
  test of the server.
- `node:22-bookworm-slim` in `bitwarden-cli`: Node 22 is a supported LTS line until 2027-04.
- Loki's `k8s-sidecar:2.5.0` and Redis `8.2.4`: what the newest charts ship.

## Resuming

Rerun the check above; the first unticked box is next.

## Done when

Every box is ticked or moved to `QUEUE.md` with a reason, and this file moves to `old-queues/`.
