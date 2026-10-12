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

- [ ] **Mealie v3.20.1 -> v3.28.0.** Since v3.21 Mealie refuses an OIDC login whose
      `email_verified` claim isn't true, and Authentik's default `email` scope mapping returns
      `"email_verified": False`. Before upgrading: a scope mapping in the Mealie blueprint
      returning `request.user.email` with `email_verified: True`, set as the mealie provider's
      `email` mapping in place of the default (the provider was made in Authentik's UI; adopt it
      into a blueprint first). Not `OIDC_REQUIRES_EMAIL_VERIFICATION=false`. v3.26 also limits
      what Mealie fetches (recipe imports, images, webhooks, recipe actions) to public internet
      addresses; anything on the LAN or tailnet needs `HTTP_ALLOW_LIST`. v3.25 moved
      `/api/auth/refresh` to POST (only matters to API clients). Mealie's database is already
      on 17.11.
- [ ] **MetalLB 0.15.2 -> 0.16.1** (`system/metallb-system/metallb/`, captured). 0.16 makes
      frr-k8s the default BGP backend; this cluster uses L2 only (one `L2Advertisement`, no
      `BGPPeer`), so set the chart's FRR and frr-k8s off rather than gain a component it doesn't
      use. Metrics are HTTPS only from 0.16 (kube-rbac-proxy is gone): after the upgrade, check
      the two ServiceMonitors' targets are up in Prometheus. MetalLB carries every LoadBalancer
      address: watch Traefik's address answer during the speakers' rollout.
- [ ] **Calico v3.32.2 -> v3.33.0** (`system/tigera-operator/tigera-operator/`, captured). Felix
      now programs the IPIP cluster routes instead of BIRD, the CNI config moves to
      `cniVersion 1.0.0`, and Whisker's backend is HTTPS only. The nodes are LXC containers, so
      check routes and pod-to-pod traffic across nodes afterwards. The HelmRelease sets no
      `crds:` policy, so Flux won't update the CRDs on upgrade: apply them from the release
      first, or set `upgrade.crds: CreateReplace`.
- [ ] **Authentik 2026.5 -> 2026.8** (chart 2026.5.6 -> 2026.8.3; the LDAP and proxy outposts
      follow the server). Three feature releases: read each one's upgrade notes. Every
      forward-auth and OIDC login depends on it; test one of each afterwards. Take a fresh
      backup of its database first.
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
