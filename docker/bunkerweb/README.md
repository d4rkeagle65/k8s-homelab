# BunkerWeb WAF for ManicTime's public endpoint

BunkerWeb (open-source, ModSecurity + OWASP CRS, with a web UI) in front of
ManicTime's public [Tailscale Funnel](../tailscale/README.md) endpoint. It runs
on the same Docker VM as the funnel so the funnel reaches it over loopback.

## Request path

```
public client ──TLS──▶ Tailscale Funnel (:443, this VM)
                           │  terminates TLS, forwards HTTP, Host = <node>.<tailnet>.ts.net
                           ▼
                       BunkerWeb  (127.0.0.1:8080, this stack)
                           │  WAF inspects the request
                           │  rewrites Host + SNI to manictime.<domain>
                           ▼
                       traefik-isolated ingress ──▶ manictime-server (cluster)
```

LAN clients on the isolated VLAN still reach ManicTime **directly** at
`manictime.<domain>` (the cluster ingress), not through this WAF. Only the
public funnel path is inspected, which is the only path that needs it.

## Why these settings

- **`SERVER_NAME` is the node's `*.ts.net` FQDN**, not `manictime.<domain>`.
  Tailscale serve forwards the client's Host header unchanged, and that is the
  funnel URL (`https://<node>.<tailnet>.ts.net`), so the WAF has to answer for
  it or its default server drops the request.
- **HTTP only** (`HTTPS_PORT` empty, both redirects off): the funnel already
  terminated TLS, so BunkerWeb serves plain HTTP on loopback.
- **`REVERSE_PROXY_CUSTOM_HOST` + SNI = `manictime.<domain>`**: the upstream is
  reached over HTTPS and traefik-isolated routes by Host, so the WAF sends a
  clean Host and matching SNI rather than the `*.ts.net` name.
- **DetectionOnly + raised body limits**: the WAF logs rule hits but blocks
  nothing until reviewed, and a large ManicTime sync is never hard-capped
  (`ProcessPartial`, no-files limit raised). See the comments in
  `compose.yaml`.

## First deploy (owner)

This is a new Dockhand stack, so it is set up by hand once; after that, merges
to `main` redeploy it through its webhook (added in the funnel-repoint PR).

1. **Create the Key Vault secrets** (`kv-hsn-1`), one per `.env` reference:
   - `TAILSCALE-FUNNEL-HOST` — the node's public MagicDNS FQDN,
     `<node>.<tailnet>.ts.net` (the same URL the ManicTime client points at).
   - `BUNKERWEB-UI-ADMIN-PASSWORD` — the web UI admin password (a lowercase, an
     uppercase, a digit and a special character; at most 72 bytes).

   `BASE-DOMAIN` and `OWNER-USERNAME` (the UI admin username) already exist.
2. **Create the stack in Dockhand** from this folder (`docker/bunkerweb/`), with
   the Azure Key Vault secret provider bound and its selector empty
   (docker/CLAUDE.md). Deploy it. Dockhand resolves the `.env` references at
   deploy.
3. **Reach the UI over an SSH tunnel** (it is published on loopback only):
   `ssh -L 7000:127.0.0.1:7000 <the VM>`, then open `http://127.0.0.1:7000`.
   The admin is seeded from the environment and the setup wizard is skipped
   (`OVERRIDE_ADMIN_CREDS`), so just log in with `OWNER_USERNAME` and the
   `BUNKERWEB-UI-ADMIN-PASSWORD`. **Don't set a UI server name in a wizard:**
   BunkerWeb would then serve the UI only at that hostname and redirect `:7000`
   to it (see "Locked out" below).

**Locked out of the UI** (it redirects `:7000` to a hostname that doesn't
resolve, from an earlier wizard run): the UI server name lives in the
`bw-storage` volume's database and env can't override it, so reset it — that
volume holds only UI/WAF state here, nothing you can't recreate:

```sh
docker rm -f bunkerweb
docker volume rm $(docker volume ls -q | grep bw-storage)   # confirm the name first
```

Then redeploy in Dockhand. With the admin seeded from env the wizard is skipped,
no server name is set, and `:7000` works.

## Verify (owner)

- On the VM, the WAF reaches ManicTime through itself (DetectionOnly, so a 200,
  not a block), sending the funnel's Host:
  `curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: <node>.<tailnet>.ts.net' http://127.0.0.1:8080/`
- The web UI shows the `bunkerweb` instance up and the ManicTime service
  configured. Until the funnel is repointed (next PR), no live traffic reaches
  it yet.

## Blocking mode

`MODSECURITY_SEC_RULE_ENGINE` is `On`: the WAF enforces CRS rules. It ran in
DetectionOnly first so the hits from a real sync could be reviewed in the UI
(**Reports** / **Logs**); if a legitimate request trips a rule, add a CRS
exclusion in the UI (or narrow the rule) rather than returning to DetectionOnly.

The rollout sequence, the in-cluster CRS WAF this replaces, and what is left are
in [`QUEUE-manictime-waf.md`](../../QUEUE-manictime-waf.md).
