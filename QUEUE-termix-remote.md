# QUEUE-termix-remote: Termix through the Cloudflare tunnel, for a few source addresses only

## The ask

The owner, 2026-11-03, remote and about to redeploy the Tailscale subnet router (the one way in):
a way to reach Termix (browser SSH) without Tailscale, restricted to an IP scope, as a
Kubernetes Ingress rather than a Cloudflare Access application.

## Settled

- **A hostname of its own,** `termix-remote.${BASE_DOMAIN}`, through the tunnel to Traefik's `web`
  entry point. Termix's own name redirects HTTP to HTTPS, and the tunnel reaches Traefik on port
  80, so it would loop (kubernetes/CLAUDE.md, "Ingress").
- **The allowed sources are NetBox's:** prefixes tagged `termix-remote` (the office's static
  address, a /32), written to Key Vault as `TERMIX-REMOTE-SOURCES` by the facts sync (a view,
  comma-separated), reaching the cluster as the variable `TERMIX_REMOTE_SOURCES`. Allowing another
  place is tagging its prefix in NetBox. No address is in git.
- **Traefik reads the client from `X-Forwarded-For`,** which Cloudflare sets: `web` trusts forwarded
  headers from the pod network only (`${CALICO_POD_CIDR}`, where `cloudflared` runs), and the
  route's IP allow-list skips every pod-network address in the header (`ipStrategy.excludedIPs`),
  so it takes the first address beyond `cloudflared` whether or not Traefik appended its own.
  Traffic from the LAN reaches `web` from outside the pod network, so nothing it sends is trusted.
  Every tunnel app then sees real client addresses instead of `cloudflared`'s.
- **Fail closed:** the route carries the allow-list and Authentik's forward auth; a middleware
  that's missing makes Traefik refuse the route (404), never serve it open. The route and its
  middleware go in before the tunnel Ingress that publishes the name.
- **Defence in depth:** past the address check, Authentik's login, then Termix's own SSH
  credentials per host.

## Steps

- [x] NetBox: the tag `termix-remote` and the office's /32, tagged (mine, through the API).
- [x] 1. The view `termix_remote_sources` (prefixes tagged `termix-remote`, sorted,
      comma-separated) mapped as `TERMIX-REMOTE-SOURCES` (#551). Left, the owner's:
      `-FromNetBox` made the secret (#552); check task 1558 agrees.
- [ ] 2. PR (this one): `TERMIX_REMOTE_SOURCES` in cluster-secrets (plain: a Middleware reads it,
      not a Secret's `data:`), merged before anything reads it. After merge: it's in the live
      cluster-secrets Secret (mine).
- [ ] 3. PR: Traefik's `web` trusts forwarded headers from `${CALICO_POD_CIDR}`; the Middleware
      `termix-remote` (ipAllowList, sourceRange `[${TERMIX_REMOTE_SOURCES}]`, excludedIPs the pod
      CIDR); Termix's Ingress for `termix-remote.${BASE_DOMAIN}` on `web` with it and Authentik's.
      Checked live before step 4: from here (the house's own address, not allowed) the route
      answers 403 through Traefik directly with a forged header naming the office, and 403 without.
- [ ] 4. PR: the tunnel Ingress for `termix-remote.${BASE_DOMAIN}` (traefik's
      `ingress-cloudflare-tunnel.yaml`). Checked live: from here, 403; the owner, from the office,
      reaches Authentik's login.

## Resume

Read the unchecked step. Every PR is the owner's to merge; each step's check is mine.

## Done when

From the office the owner reaches Termix through Authentik; from anywhere else the name answers
403; and the allowed sources come only from NetBox.

## Checked, negative

- Traefik's `private-networks` middleware: it reads the connection's address, which behind the
  tunnel is always `cloudflared`'s, so it can't tell one client from another there.
