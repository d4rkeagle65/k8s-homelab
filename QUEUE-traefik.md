# Replace ingress-nginx with Traefik

ingress-nginx is retired: no releases or security fixes since March 2026
(https://www.kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/). It also
takes internet traffic: the Cloudflare tunnel routes for babybuddy, babybuddy-mcp
and immich point at `ingress-nginx-controller`.

Traefik's NGINX provider reads `nginx`-class Ingresses and their nginx
annotations as they are, so app manifests don't change to switch. Its migration
guide: https://doc.traefik.io/traefik/migrate/nginx-to-traefik/

Addresses are named, not written, here (the repo is public):
`INGRESS_IP` is the main ingress-nginx address today; `TRAEFIK_IP` is the address
Traefik runs on while both run side by side. Both are fields of the
`cluster-secrets` Vaultwarden item and entries in
`kubernetes/secrets/cluster-secrets.yaml` before anything uses them.

## What depends on ingress-nginx today

- `ingress-nginx`: 26 Ingresses (class `nginx`), on `INGRESS_IP`.
- `ingress-nginx-isolated`: ManicTime's 2 Ingresses (class `nginx-isolated`), on
  `ISOLATED_INGRESS_IP`.
- nginx annotations in use, all listed as supported by Traefik's provider:
  `auth-url`, `auth-signin`, `auth-response-headers` (babybuddy's Authentik
  login), `proxy-body-size` (6 apps), `proxy-read-timeout`,
  `proxy-send-timeout`, `ssl-redirect`.
- Cloudflare tunnel routes to `ingress-nginx-controller`: `bb-cloudflare-tunnel`,
  `bb-mcp-cloudflare-tunnel`, `ingress-nginx-cloudflared-immich`
  (`apps/ingress-nginx/ingress-nginx-extras/`).
- SMTP: TCP 8025 to `smtp-relay/smtp-oauth-relay` (`tcp:` in the ingress-nginx values).

## Batch 1: install Traefik alongside (done 2026-09-30)

- [x] Add `TRAEFIK_IP` to Vaultwarden and `kubernetes/secrets/cluster-secrets.yaml`;
      check it synced. Push that first. It must be a free address inside the
      MetalLB pool (`METALLB_POOL_RANGE`); outside it, the Service stays
      `<pending>` with "is not allowed in config".
- [x] New `.handwritten` app `kubernetes/apps/traefik/traefik/`: Traefik chart
      (v41.6.0 at last check, Traefik v3.7), HelmRepository
      `https://traefik.github.io/charts`, `wait: true`.
  - Providers: `kubernetesIngressNGINX` (IngressClass `nginx`); `kubernetesCRD`
    for the TCP route. Leave the plain `kubernetesIngress` provider off, so the
    same Ingress doesn't get two routers.
  - Entry points: web 80, websecure 443, smtp 8025.
  - Service: LoadBalancer on `${TRAEFIK_IP}`, `externalTrafficPolicy: Local` (as
    nginx has, so apps see client IPs).
  - Status publishing off until cutover: both controllers would otherwise
    overwrite each Ingress's `status.loadBalancer` in a loop (Traefik's guide,
    "Ingress Status Race Condition").
  - `IngressRouteTCP` for port 8025 → `smtp-relay/smtp-oauth-relay:8025`
    (`HostSNI(\`*\`)`: plain TCP, STARTTLS passes through), created by the chart
    (`extraObjects`) in the relay's namespace.
  - The chart's own `traefik` IngressClass off (it would become the default).
- [x] Check: `flux get kustomizations`, Traefik pods Running, Service has `TRAEFIK_IP`.

## Batch 2: test on TRAEFIK_IP (no merge; Pi-hole only)

- [ ] Point a few test names at `TRAEFIK_IP` in Pi-hole, one at a time (see
      "Pi-hole" below), and flush the client's DNS cache (`ipconfig /flushdns`).
- [ ] babybuddy: log in through Authentik. Known risk: Traefik's `auth-url`
      "behaves differently than NGINX". If it fails, switch that Ingress to
      Authentik's Traefik endpoint (`/outpost.goauthentik.io/auth/traefik`, a
      Traefik ForwardAuth middleware), per Authentik's docs.
- [ ] immich: upload a large file (`proxy-body-size`). Traefik's 60s limit on
      reading a request is turned off (`readTimeout: 0`), as nginx has none.
- [ ] `http://` on an app with TLS still redirects to `https://`. ingress-nginx
      does that by default for any Ingress with TLS; Traefik's NGINX provider
      only where `nginx.ingress.kubernetes.io/ssl-redirect` says so, and there's
      no global switch that wouldn't also break the Cloudflare tunnel's plain-HTTP
      routes. If it doesn't, add `ssl-redirect: "true"` to those Ingresses.
- [ ] A media app, homeassistant, obsidian: pages and websockets load.
- [ ] SMTP: `Send-MailMessage -SmtpServer <TRAEFIK_IP> -Port 8025 -UseSsl ...`.
- [ ] Put the test names back to `INGRESS_IP` if anything failed; fix; repeat.

## Batch 3: cutover (one merge + DNS; short outage)

Decide first how clients reach the ingress:
- **Keep `INGRESS_IP` (recommended):** move the address to Traefik, so anything
  using the IP directly (router port forwards, other DNS) keeps working. Remove
  `metallb.io/loadBalancerIPs` use of it from nginx (pin nginx to any other free
  address) and set Traefik's to `${INGRESS_IP}`, in the same merge.
- **Or move to `TRAEFIK_IP`:** flip every Pi-hole record from `INGRESS_IP` to
  `TRAEFIK_IP`. Only clients using Pi-hole follow; check the router's port
  forwards and anything else that names the IP.

Then, in the same merge:
- [ ] Point the three Cloudflare tunnel Ingresses at Traefik's Service
      (`traefik.traefik`, port 80) instead of `ingress-nginx-controller`.
- [ ] Remove `tcp:` from the ingress-nginx values (Traefik has 8025 now).
- [ ] Turn Traefik's status publishing on.
- [ ] Check every app, the three tunnel routes from outside, Vaultwarden's SMTP
      test email. Rollback: revert the merge (and the Pi-hole records).

## Batch 4: remove the main ingress-nginx (one merge)

- [ ] Keep the `nginx` IngressClass as a standalone object in git
      (`controller: k8s.io/ingress-nginx`): Traefik finds the Ingresses through
      it, and uninstalling the chart would delete it (Traefik's guide, "Preserve
      the IngressClass").
- [ ] Remove the `ingress-nginx` release and its HelmRepository if unused; drop
      the nginx-only config in `ingress-nginx-extras` (log format and custom
      header ConfigMaps).
- [ ] Delete the `ingress-nginx` namespace by hand afterwards (namespaces aren't
      pruned).

## Batch 5: ingress-nginx-isolated (repeat batches 1-4 for ManicTime)

- [ ] A second Traefik release on `ISOLATED_INGRESS_IP`, NGINX provider on the
      `nginx-isolated` IngressClass (controller `k8s.io/ingress-nginx-isolated`).
      Mirrors today's setup, so the isolated VLAN still reaches only ManicTime.
- [ ] Test, cut over, remove `ingress-nginx-isolated`.

## Pi-hole: pointing names at TRAEFIK_IP

Pi-hole v6 keeps local DNS records in `/etc/pihole/pihole.toml` (`[dns]`,
`hosts = [ "IP HOSTNAME [HOSTNAME ...]", ... ]`) and reloads when the file is
rewritten. CNAME records point at names, so they follow on their own. On the
Pi-hole (prefix with `docker exec <container>` if it runs in Docker):

```bash
OLD='<INGRESS_IP>'; NEW='<TRAEFIK_IP>'           # the real addresses
OLD_RE=$(printf '%s' "$OLD" | sed 's/\./\\./g')
sudo cp /etc/pihole/pihole.toml "/etc/pihole/pihole.toml.bak-$(date +%F-%H%M)"
grep -n "\"$OLD_RE " /etc/pihole/pihole.toml     # records on OLD

# Batch 2, one name at a time (a record listing several names moves all of them):
NAME='<hostname>'
sudo sed -i "s/\"$OLD_RE \($NAME\b\)/\"$NEW \1/" /etc/pihole/pihole.toml

# Batch 3, if moving everything to TRAEFIK_IP instead of moving the address:
sudo sed -i "s/\"$OLD_RE /\"$NEW /g" /etc/pihole/pihole.toml

grep -n "\"$NEW " /etc/pihole/pihole.toml && nslookup "$NAME" 127.0.0.1
# Rollback: copy the .bak file back over pihole.toml.
```

## Later (optional)

- [ ] Replace nginx annotations with native Traefik middlewares or Gateway API
      routes, one app at a time. Nothing forces this while the NGINX provider
      is maintained.
