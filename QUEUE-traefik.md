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

## Batch 2: test on TRAEFIK_IP (done 2026-09-30)

- [x] Test names pointed at `TRAEFIK_IP` in Pi-hole: immich (large upload),
      sonarr, obsidian, SMTP on 8025 and the http→https redirect all work.
- [x] babybuddy **fails** through Traefik with Authentik's "configuration
      error": its `auth-url` is Authentik's `/auth/nginx` endpoint, which needs
      the `X-Original-URL` header only nginx sends. Traefik's forward auth sends
      `X-Forwarded-Proto/Host/Uri`, which `/auth/traefik` reads. Checked from
      inside the cluster: `/auth/traefik` answers 302 to the Authentik login.
      Switched at cutover (not before: the `bb` tunnel route goes through nginx
      until then). Traefik ignores `auth-signin`; `/auth/traefik` redirects to
      the login itself.
- [x] The tunnel routes, simulated from inside the cluster (Host header to each
      controller's Service): Traefik answers exactly as nginx for bb-mcp and
      immich. Plain HTTP to immich redirects to HTTPS on both, so the tunnel
      must be using the immich route's port 443.

## Batch 3: cutover (one merge; short outage)

Keeps the main address: `INGRESS_IP` moves from ingress-nginx to Traefik, so
clients using the address directly (SMTP on 8025, port forwards) keep working.
nginx never pinned it; MetalLB handed it out.

- [x] 3a: `INGRESS_IP` in Vaultwarden and `kubernetes/secrets/cluster-secrets.yaml`.
- [ ] Before merging: point the Batch 2 test names back to `INGRESS_IP` in
      Pi-hole (nginx serves them until the merge, Traefik after).
- [ ] 3b, one merge:
  - Traefik's Service asks for `${INGRESS_IP}`.
  - ingress-nginx's Service becomes `ClusterIP`, releasing the address; nginx
    keeps running, so reverting the merge is the rollback. Its `tcp:` entry and
    `externalTrafficPolicy` (invalid on ClusterIP) go.
  - The three Cloudflare tunnel Ingresses move to the `traefik` namespace
    (an Ingress can only use a Service in its own namespace), pointing at the
    `traefik` Service on the same ports.
  - babybuddy's `auth-url`: `/auth/nginx` → `/auth/traefik`.
- [ ] After: the Traefik Service has `INGRESS_IP` (if it stays `<pending>`,
      MetalLB didn't retry once nginx released it: see below); every app; bb,
      bb-mcp and immich from outside the LAN; an SMTP send; babybuddy login.

If Traefik's Service stays `<pending>` after nginx's shows no external IP:
`kubectl -n metallb-system rollout restart deploy/controller`.

## Batch 4: remove the main ingress-nginx (one merge)

- [x] 4a: the chart's `nginx` IngressClass gets `helm.sh/resource-policy: keep`
      (`controller.ingressClassResource.annotations`), so uninstalling the chart
      leaves it: Traefik finds the Ingresses through it (Traefik's guide,
      "Preserve the IngressClass"). Check:
      `kubectl get ingressclass nginx -o jsonpath="{.metadata.annotations}"`.
- [x] 4b, one merge (merged 2026-09-30):
  - The `nginx` IngressClass as a standalone object in the Traefik app
    (`ingressclass-nginx.yaml`), so Flux takes it over from Helm.
  - `apps/ingress-nginx/` removed (the release, `ingress-nginx-extras`' nginx-only
    ConfigMaps, the namespace files). Flux uninstalls the chart; the Namespace
    itself stays (prune disabled). The `ingress-nginx` HelmRepository stays:
    `ingress-nginx-isolated` uses it until Batch 5.
  - `authentik-extras`' `authentik-ingress-nginx` ExternalName Service removed
    (pointed at the nginx controller; nothing used it).
  - Traefik's status publishing on (`publishService`).
  - `TRAEFIK_IP`'s ExternalSecret entry removed.
- [ ] After: the IngressClass is still there; apps work; Ingresses show
      `INGRESS_IP` as their address; no `ingress-nginx-admission` in
      `kubectl get validatingwebhookconfigurations` (it would block every
      Ingress change). Then by hand: `kubectl delete namespace ingress-nginx`,
      delete the `TRAEFIK_IP` field in Vaultwarden, and run `all` to check
      capture brings nothing back.

## Batch 5: ingress-nginx-isolated (ManicTime) (done 2026-10-01)

Batches 1-4 again for the `nginx-isolated` IngressClass (controller
`k8s.io/ingress-nginx-isolated`), smaller: one app, no Authentik, no tunnel. It
is tested from inside the cluster instead of on a temporary address, so no new
variable or Pi-hole change.

- [x] 5a: second Traefik release, `apps/traefik-isolated/` (`.handwritten`):
      NGINX provider on `nginx-isolated` only, CRD provider off, CRDs left to
      the main release (`crds: Skip`), Service `ClusterIP`, no status
      publishing. Test: the Host/SNI check from inside the cluster against
      `traefik-isolated` and `ingress-nginx-isolated-controller` gives the same
      answers for `manictime.<domain>`, with the real certificate.
- [x] 5b, cutover (one merge, merged 2026-10-01): `traefik-isolated`'s Service becomes
      `LoadBalancer` on `${ISOLATED_INGRESS_IP}` with `externalTrafficPolicy:
      Local` (keeps client addresses, which ManicTime logs); the
      `ingress-nginx-isolated` Service becomes `ClusterIP`; its `nginx-isolated`
      IngressClass gets `helm.sh/resource-policy: keep`. Test from a client on
      the isolated VLAN (the firewall only allows that address).
- [x] 5c, removal (one merge, merged 2026-10-01): the `nginx-isolated` IngressClass as a standalone
      object in the `traefik-isolated` app; `apps/ingress-nginx-isolated/` removed;
      status publishing on; the `ingress-nginx` HelmRepository removed (nothing
      uses it then). By hand afterwards: delete the `ingress-nginx-isolated`
      namespace, check no `ingress-nginx-isolated-admission` webhook is left,
      `helm repo remove ingress-nginx` on the PC that runs capture (capture
      writes a HelmRepository for every `helm repo list` entry), run `all`.

## Batch 6: native Traefik Ingresses (no more nginx classes)

Apps move from the `nginx`/`nginx-isolated` classes (Traefik's NGINX provider,
nginx annotations) to `traefik`/`traefik-isolated` (Traefik's own Ingress
provider), a few at a time. Each Ingress is read by exactly one provider,
chosen by its class, so moving one only changes that app. Translations:

| nginx annotation | Traefik |
|---|---|
| TLS section, default HTTP to HTTPS redirect | `traefik.ingress.kubernetes.io/router.middlewares: traefik_redirect-https@kubernetescrd` (a router serves both ports; the certificate is picked by host on 443) |
| `ssl-redirect: "false"` (bb, bb-mcp: the tunnel uses HTTP) | no redirect middleware |
| `proxy-body-size`, `proxy-read-timeout`, `proxy-send-timeout` | dropped: Traefik has no body limit, and `readTimeout: 0` is set |
| `auth-url`, `auth-response-headers`, `auth-signin` (babybuddy) | a `forwardAuth` Middleware to Authentik's `/auth/traefik` |
| `cert-manager.io/cluster-issuer` | unchanged |

- [x] 6a: the `traefik` and `traefik-isolated` IngressClasses (not default),
      the Ingress provider on each (that class only, publishing status), and the
      `redirect-https` Middleware. Moves no app.
- [x] 6b: pilot, `immichpt` (promoted Ingress in `immich-extras`).
- [x] 6c: the rest without auth: emby, authentik, immich, obsidian,
      homeassistant, the media apps (`apps/media/*`, `test/media/jackett`).
- [x] 6d: babybuddy (forwardAuth Middleware), babybuddy-api, babybuddy-mcp.
- [x] 6e: ManicTime to `traefik-isolated` (the HTTP to HTTPS redirect on its
      `web` entry point: no tunnel there).
- [ ] 6f: remove the `nginx` and `nginx-isolated` IngressClasses and both NGINX
      providers once nothing uses them.

Each move: the in-cluster Host/SNI check before and after, then the app in a
browser (and a hard refresh).

## Pi-hole: pointing names at a new address

Pi-hole v6 keeps local DNS in `/etc/pihole/pihole.toml` (`[dns]`) and reloads
when the file is rewritten. The app names are **CNAMEs**, not address records:
`cnameRecords` has `"<app>.<domain>,k8snginx.<local domain>"` (ManicTime's
target is `k8snginx-isolated.<local domain>`), and `hosts` has the one address
record each target resolves to, `"<ip> k8snginx.<local domain>"`. So:

- **Moving every name at once** means changing that one `hosts` entry, or, as
  in Batch 3, moving the address itself to the new controller (no DNS change).
- **Testing one name** means pointing its CNAME at a second target name with
  the test address. (Batch 2's per-name command edited `hosts` entries only,
  so it matched nothing for CNAME'd names; some Batch 2 tests likely still
  reached nginx. babybuddy did reach Traefik, since it failed there.)

On the Pi-hole (prefix with `docker exec <container>` if it runs in Docker):

```bash
sudo cp /etc/pihole/pihole.toml "/etc/pihole/pihole.toml.bak-$(date +%F-%H%M)"
LOCAL='<local domain>'; TEST_IP='<test address>'; TARGET="k8stest.$LOCAL"
# Once: an address record for the test target, as the first `hosts` entry.
sudo sed -i "0,/^  hosts = \[/s//&\n    \"$TEST_IP $TARGET\",/" /etc/pihole/pihole.toml
# Per name: repoint its CNAME (OLD_TARGET is what it points at today).
APP='<app>.<domain>'; OLD_TARGET="k8snginx-isolated.$LOCAL"
sudo sed -i "s/\"$APP,$OLD_TARGET\"/\"$APP,$TARGET\"/" /etc/pihole/pihole.toml
grep -n -E "$TARGET|\"$APP," /etc/pihole/pihole.toml && dig +short "$APP" @127.0.0.1
# Rollback: copy the .bak file back over pihole.toml.
```

Check the `hosts = [` line matches your file (`grep -n "hosts = \[" ...`)
before running the first `sed`.

### `local=/<local domain>/` (added 2026-09-30)

`misc.dnsmasq_lines` has `local=/<local domain>/`, so Pi-hole answers that domain
only from its own records. Before, it forwarded what it couldn't answer (e.g.
the AAAA lookup for `k8snginx.<local domain>`, which has only an A record) to
the DHCP server by conditional forwarding, which never replied. Vaultwarden's
resolver waits for the AAAA answer, so its SSO discovery timed out. Side effect:
hostnames only the DHCP server knows no longer resolve under that domain. The
better long-term fix is the DHCP server answering, then removing the line.

## Later (optional)

- [ ] Native Traefik Ingresses and the class rename: now Batch 6.
