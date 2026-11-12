# ManicTime over Tailscale Funnel, behind a self-hosted WAF

**The ask** (owner, 2026-11-11): the ManicTime desktop client on a laptop and a Windows 365
VM (neither can run Tailscale) must reach the self-hosted ManicTime server over a public
HTTPS endpoint, fronted by a self-hosted WAF with a management GUI, without widening
ManicTime's isolation beyond that one endpoint. Auth stays ManicTime's own OAuth2
password-to-bearer-token login; the WAF is defence in depth (full in-path POST-body
inspection, which a forward-auth check could not do, and ManicTime has no OIDC).

## Decisions settled

- **Pipe: Tailscale Funnel** on the existing subnet-router node (`docker/tailscale/`), already
  live (#592). Funnel terminates TLS at `https://<node>.<tailnet>.ts.net` and reverse-proxies
  the request on, forwarding the client's Host header unchanged.
- **WAF: BunkerWeb**, the `bunkerity/bunkerweb-all-in-one` image, as a **Docker stack on the
  funnel's VM** (`docker/bunkerweb/`) — NOT in the cluster.
  - Why Docker, not in-cluster: the BunkerWeb Helm chart is a six-pod platform (data plane +
    scheduler + its own Ingress controller + UI + MariaDB + Redis, cluster-wide RBAC, docs
    minimum 2 vCPU / 8 GB), far too heavy for this memory-constrained cluster in front of one
    non-critical app. The all-in-one image is one container (instance + scheduler + UI +
    embedded Redis + SQLite). Docker Compose is BunkerWeb's best-supported model, and
    co-locating with the funnel puts the WAF at the public edge and lets the funnel reach it
    over loopback.
  - Why BunkerWeb over the CRS image first chosen (#591): the owner wanted a management GUI to
    review false positives and tune before blocking; the bare CRS image has none (logs only).
  - Why BunkerWeb over SafeLine (also weighed): BunkerWeb is fully open source (AGPL) and does
    not phone home; SafeLine's detector is a closed binary that fetches rule updates.
- **This replaces the in-cluster CRS WAF** (`kubernetes/prod/apps/manictime/manictime-waf/`,
  live from #591 in DetectionOnly). It is retired in PR #3 below, not before BunkerWeb is
  proven in the funnel path.
- **Only the public funnel path is WAF'd.** LAN clients on the isolated VLAN keep reaching
  `manictime.<domain>` (the cluster ingress) directly.
- **Start in ModSecurity DetectionOnly, flip to blocking after review** of a real sync.

## Current live state (what is already in the cluster / tailnet)

- BunkerWeb stack: deployed on the funnel VM, DetectionOnly, UI reachable; the setup wizard is
  skipped (#594, #598, #600).
- Funnel: proxies `:443` to the local BunkerWeb on `http://127.0.0.1:8080` (#599, confirmed by
  `tailscale serve status`); `tag:subnet-router` has the `funnel` nodeAttr and the tailnet's
  HTTPS certs are on (#591/#593). BunkerWeb -> ManicTime confirmed (curl 200); a full client
  sync still pending.
- `manictime-waf` CRS release: still live (DetectionOnly) until PR #3 below merges — the public
  path currently runs two WAFs in series (BunkerWeb, then the in-cluster CRS).
- Webhook for the `bunkerweb` stack: PR #601 (stack 19), plus the owner enabling it in Dockhand.

## Rollout — three PRs, each verified before the next

**PR #1 — the BunkerWeb stack** (`docker/bunkerweb/`, DONE in this branch): compose.yaml,
.env, README.md, the `bunkerweb` block in `docker/secret-names.yaml`. Deploys nothing by
itself (new stack; no webhook yet). Owner steps are in `docker/bunkerweb/README.md`:
create the three Key Vault secrets, create + deploy the Dockhand stack, reach the UI over an
SSH tunnel, verify the WAF proxies to ManicTime on the LAN.

**PR #2 — repoint the funnel** (`docker/tailscale/`, merged #599): the serve config
`Proxy` now points at `http://127.0.0.1:8080` (the local BunkerWeb WAF) instead of
`https://manictime.${BASE_DOMAIN}`; comments and the stack README updated, and the now-unused
`BASE_DOMAIN` dropped from the stack's `.env`. The Host is answered by BunkerWeb's
`SERVER_NAME`, so no cluster catch-all is involved.
- **Deploy BunkerWeb first.** This only works once BunkerWeb is up on `127.0.0.1:8080` (PR #1
  deployed + verified), or the funnel has nothing to proxy to.
- **Merge from the LAN** — it redeploys the tailscale stack (a brief tailnet blip).
- Verify: a ManicTime client set to the funnel URL signs in and fully syncs, including one
  large sync (no hard cap); the WAF logs the sync in its UI.

**PR #2b — the Dockhand webhook** (`dockhand-webhooks`, DONE in this branch): a
`dockhand-bunkerweb` Provider + Alert in
`kubernetes/prod/system/external-services/dockhand-webhooks/app/webhooks.yaml`, address
`http://${DOCKHAND_ADDRESS}:3000/api/git/stacks/19/webhook`. After merge, the owner turns the
git webhook on for the `bunkerweb` stack in Dockhand; then `docker/bunkerweb/` changes
redeploy on merge (until then they are redeployed by hand).

**PR #3 — retire the in-cluster CRS WAF** (`kubernetes/`, DONE in this branch): deleted the
`manictime-waf/` release folder and re-enabled the `manictime-server` chart's own Ingress
(`ingress.enabled: true`, host `manictime.${BASE_DOMAIN}`, class `traefik-isolated`, tls
`manictime-tls`, cluster-issuer `cloudflare-letsencrypt`); `generate` dropped `manictime-waf`
from the namespace kustomization and rebuilt the HelmRelease. No hostless catch-all is needed —
BunkerWeb sends a clean Host upstream.
- Merging prunes the CRS WAF Deployment/Service/Ingress and applies the chart Ingress, so the
  public path becomes funnel -> BunkerWeb -> traefik-isolated -> manictime-server, and LAN
  clients go traefik-isolated -> manictime-server directly (no in-cluster WAF).
- Verify after merge: LAN clients and the funnel path both still reach ManicTime; the
  `manictime-waf` pod is gone.

**Switch to blocking** (prepared, merge after review): sets `MODSECURITY_SEC_RULE_ENGINE: "On"`
in `docker/bunkerweb/compose.yaml`. Merge only after reviewing the DetectionOnly hits from a
real sync in the UI and adding any needed CRS exclusions; it redeploys via the webhook (or by
hand). Flipping before that review risks blocking legitimate sync traffic.

## New Key Vault secrets (owner creates in `kv-hsn-1`)

- `TAILSCALE-FUNNEL-HOST` — the node's public MagicDNS FQDN `<node>.<tailnet>.ts.net`.
- `BUNKERWEB-UI-ADMIN-PASSWORD` — the web UI admin password.

The UI admin username reuses the shared `OWNER_USERNAME` (`OWNER-USERNAME`) secret, not a
new one.

## Known follow-ups / open points

- **ManicTime advertises its URL from the request Host** (the client "Next" / `GetClientSettings`
  12007): ManicTime builds the URL it hands clients from the request and only trusts forwarded
  headers from proxies it lists (loopback by default). Fix (PR-2): BunkerWeb sends the funnel
  Host upstream (`REVERSE_PROXY_CUSTOM_HOST: ${TAILSCALE_FUNNEL_HOST}`, SNI still
  manictime.<domain>); the `manictime-server` ingress routes `${TAILSCALE_FUNNEL_HOST}` (TLS stays
  manictime.<domain>, since the funnel name is Tailscale's domain); and
  `settings.extra.web.reverseProxy.networks: ["*"]` trusts traefik so X-Forwarded-Host/Proto are
  honoured. **Seed-once gotcha:** the chart's init container writes `ManicTimeServerSettings.json`
  only if absent, so an existing deployment does NOT pick up the new `web.reverseProxy` until the
  file is reset on the PVC and the pod restarts:

  ```sh
  kubectl -n manictime exec deploy/manictime-server -- rm -f /app/Data/ManicTimeServerSettings.json
  kubectl -n manictime rollout restart deploy/manictime-server
  ```

- **UI exposure**: PR #1 publishes the UI on loopback only (plaintext), reached over SSH. A
  permanent LAN exposure — the repo's pattern is a Traefik `external-services` private route
  (`websecure` + `private-networks` middleware, HTTPS via cert-manager) at
  `bunkerweb.${BASE_DOMAIN}`, pointing at the VM's eth1 address:7000 — is a separate additive
  change once the owner decides. BunkerWeb's own admin login is the auth either way.
- **Real client IP / per-client limits**: the funnel reaches the WAF from 127.0.0.1 and
  forwards no client-IP header, so BunkerWeb's IP-keyed throttles (`USE_LIMIT_REQ`,
  `USE_LIMIT_CONN`, `USE_BAD_BEHAVIOR`) are disabled — at 2r/s they 429'd a real sync and
  bad-behavior would ban the loopback for a day. To restore per-client limiting, carry the
  client IP with Tailscale Funnel's PROXY-protocol option + `USE_PROXY_PROTOCOL: "yes"` and
  `REAL_IP_HEADER: proxy_protocol`, then re-enable the limits (verify the exact Funnel flag
  first).
- **mem_limit** is a first pass (1536m); resize after a week of cAdvisor data
  (QUEUE-resource-limits.md), and watch for an OOM during a very large sync (ModSecurity
  buffers the inspected body in memory).
- Automating `TAILSCALE-FUNNEL-HOST` (the facts sync already writes `TAILSCALE-HOSTNAME`; the
  FQDN adds the tailnet domain) would remove a manual secret — not done.

## Done when

ManicTime is reachable at the funnel URL through BunkerWeb, a client fully syncs (including one
large sync, no hard cap), the in-cluster CRS WAF is gone, and the WAF is in blocking mode with
any needed exclusions, with no regression on the LAN path.

## Negative checks (came back clean)

- No existing Key Vault variable holds the node's `*.ts.net` FQDN or the tailnet domain, so a
  new secret is needed (repo grep for `ts.net` / `tailnet` / `FUNNEL`).
- No BunkerWeb setting makes a single site answer for any Host; `SERVER_NAME` must be the exact
  forwarded Host (BunkerWeb settings reference).
