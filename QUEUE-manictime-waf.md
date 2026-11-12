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

- `manictime-waf` CRS release: live, DetectionOnly, LAN path returns 200 through it (#591).
- `manictime-server` chart Ingress: off (`ingress.enabled: false`, #591); the host is served
  by `manictime-waf`'s hand-written Ingress, which has a `manictime.<domain>` rule and a
  hostless catch-all for the funnel's forwarded Host.
- Funnel: `TS_SERVE_CONFIG` proxies `:443` to `https://manictime.${BASE_DOMAIN}` (#592).
  `tag:subnet-router` has the `funnel` nodeAttr (tofu task 1694); the tailnet's HTTPS
  certificates are on (owner enabled by hand; tofu `ignore_changes` on `https_enabled`, #593).
- Public funnel end-to-end has NOT been verified by a real client sync yet.

## Rollout — three PRs, each verified before the next

**PR #1 — the BunkerWeb stack** (`docker/bunkerweb/`, DONE in this branch): compose.yaml,
.env, README.md, the `bunkerweb` block in `docker/secret-names.yaml`. Deploys nothing by
itself (new stack; no webhook yet). Owner steps are in `docker/bunkerweb/README.md`:
create the three Key Vault secrets, create + deploy the Dockhand stack, reach the UI over an
SSH tunnel, verify the WAF proxies to ManicTime on the LAN.

**PR #2 — repoint the funnel + webhook** (`docker/tailscale/`, `dockhand-webhooks`):
- In `docker/tailscale/compose.yaml`, change the serve config `Proxy` from
  `https://manictime.${BASE_DOMAIN}` to `http://127.0.0.1:8080` (the local WAF), and update
  the comments (the Host is now answered by BunkerWeb's `SERVER_NAME`, not a cluster catch-all).
- Add a `dockhand-bunkerweb` Provider + Alert to
  `kubernetes/prod/system/external-services/dockhand-webhooks/app/webhooks.yaml` with the
  Dockhand stack ID the owner got in PR #1 (next free id looked like 19; confirm with owner),
  and the owner turns the git webhook on for the stack in Dockhand.
- **Merge from the LAN** — it redeploys the tailscale stack (a brief tailnet blip).
- Verify: a ManicTime client set to the funnel URL signs in and fully syncs, including one
  large sync (no hard cap); the WAF logs the sync in its UI.

**PR #3 — retire the in-cluster CRS WAF** (`kubernetes/`):
- Delete `kubernetes/prod/apps/manictime/manictime-waf/` (the whole release folder).
- Re-enable the `manictime-server` chart's own Ingress: in
  `kubernetes/prod/apps/manictime/manictime-server/app/values.yaml` set `ingress.enabled: true`
  and restore its host rule (`manictime.${BASE_DOMAIN}`, ingressClass `traefik-isolated`, tls
  `manictime-tls`, cluster-issuer `cloudflare-letsencrypt`) — see the pre-#591 values in git
  history (`git log -p` on that file). No hostless catch-all is needed: BunkerWeb now sends a
  clean Host upstream.
- Remove `manictime-waf` from any kustomization that lists it; run
  `python scripts/backup.py generate` from the repo root and commit the result.
- Verify: LAN clients and the funnel path both still reach ManicTime (now
  funnel -> BunkerWeb -> traefik-isolated -> manictime-server).

**After PR #3 — switch to blocking**: review the WAF hits in the BunkerWeb UI against the real
sync, add exclusions, set `MODSECURITY_SEC_RULE_ENGINE: On` in `docker/bunkerweb/compose.yaml`,
redeploy.

## New Key Vault secrets (owner creates in `kv-hsn-1`)

- `TAILSCALE-FUNNEL-HOST` — the node's public MagicDNS FQDN `<node>.<tailnet>.ts.net`.
- `BUNKERWEB-UI-ADMIN-PASSWORD` — the web UI admin password.

The UI admin username reuses the shared `OWNER_USERNAME` (`OWNER-USERNAME`) secret, not a
new one.

## Known follow-ups / open points

- **UI exposure**: PR #1 publishes the UI on loopback only (plaintext), reached over SSH. A
  permanent LAN exposure — the repo's pattern is a Traefik `external-services` private route
  (`websecure` + `private-networks` middleware, HTTPS via cert-manager) at
  `bunkerweb.${BASE_DOMAIN}`, pointing at the VM's eth1 address:7000 — is a separate additive
  change once the owner decides. BunkerWeb's own admin login is the auth either way.
- **Real client IP**: the funnel reaches the WAF from 127.0.0.1, so by default every request
  looks like it comes from loopback. Harmless in DetectionOnly. Before blocking, confirm which
  header (if any) the funnel sends and, if useful, set `USE_REAL_IP` / `REAL_IP_FROM` /
  `REAL_IP_HEADER`; don't trust a client-supplied header the funnel passes through.
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
