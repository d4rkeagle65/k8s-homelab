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
   Complete first-run setup if prompted (`/setup`); log in with the admin
   credentials above.

## Verify (owner)

- On the VM, the WAF reaches ManicTime through itself (DetectionOnly, so a 200,
  not a block), sending the funnel's Host:
  `curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: <node>.<tailnet>.ts.net' http://127.0.0.1:8080/`
- The web UI shows the `bunkerweb` instance up and the ManicTime service
  configured. Until the funnel is repointed (next PR), no live traffic reaches
  it yet.

## Switch to blocking

After a real client sync has passed through (funnel repointed), review the hits
in the UI (**Reports** / **Logs**), add exclusions for any false positives, then
set `MODSECURITY_SEC_RULE_ENGINE` to `On` in `compose.yaml` and redeploy. Until
then the WAF detects but blocks nothing, so the public endpoint is guarded only
by ManicTime's own OAuth2 bearer-token login.

The rollout sequence, the in-cluster CRS WAF this replaces, and what is left are
in [`QUEUE-manictime-waf.md`](../../QUEUE-manictime-waf.md).
