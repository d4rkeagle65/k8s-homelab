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
  a YAML flow list, `[a, b]`, so `sourceRange: ${TERMIX_REMOTE_SOURCES}` stays a list once Flux
  fills it in), reaching the cluster as the variable `TERMIX_REMOTE_SOURCES`. Allowing another
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
- **Defence in depth:** past the address check, Authentik's gate, Termix's own login, then
  Termix's SSH credentials per host.
- **Authentik's gate is per name** (`termix.yaml` blueprint: `forward_single` for Termix's own
  name), so the remote name gets a provider and application of its own, bound to Termix Admins
  and on the outpost's list. Termix's OIDC provider accepts one callback; the remote name adds its
  own: Termix builds it from the name it's reached on (seen in Authentik's log).

## Steps

- [x] NetBox: the tag `termix-remote` and the office's /32, tagged (mine, through the API).
- [x] 1. The view `termix_remote_sources` (prefixes tagged `termix-remote`, sorted,
      comma-separated) mapped as `TERMIX-REMOTE-SOURCES` (#551); `-FromNetBox` made the secret
      (#552); check task 1558 agrees.
- [x] 2a. The view writes a YAML list (#555); the live cluster-secrets value parses as one.
- [x] 2. PR (#553): `TERMIX_REMOTE_SOURCES` in cluster-secrets (plain: a Middleware reads it,
      not a Secret's `data:`); it's in the live cluster-secrets Secret.
- [x] 3. The remote gate, Traefik's trust, the Middleware and Termix's remote route (#556): Traefik
      rolled with them; from here the remote route answers 403, plain and with a forged
      `X-Forwarded-For` naming the office; Termix's own name still redirects on 80 and reaches
      Authentik's login on 443. The owner saw "Termix (remote gate)" and the outpost's list in
      Authentik; the outpost loaded its 5 providers at 19:46:53.
- [x] 4. The tunnel Ingress (#557) for `termix-remote.${BASE_DOMAIN}` (traefik's
      `ingress-cloudflare-tunnel.yaml`). From the office the owner passed the address check and
      Authentik's gate.
- [x] 5. Termix signs in through Authentik's OIDC, building its callback from the name it was
      reached on, so its provider allows termix-remote's callback too (#558). The owner signs in
      from the office; from the house, through Cloudflare, 403, plain and forged.

## Resume

Read the unchecked step. Every PR is the owner's to merge; each step's check is mine.

## Done when

From the office the owner reaches Termix through Authentik; from anywhere else the name answers
403; and the allowed sources come only from NetBox.

## Checked, negative

- The public name's certificate is Cloudflare's edge (Universal SSL, Google Trust Services), not
  the cluster's Let's Encrypt: through the tunnel, TLS ends at Cloudflare.
- Inside the house the name resolves privately (ExternalDNS writes every Traefik host's record)
  to Traefik's websecure, where it has no route: it's for use from outside.

- Traefik's `private-networks` middleware: it reads the connection's address, which behind the
  tunnel is always `cloudflared`'s, so it can't tell one client from another there.
