# QUEUE: convert UI-made Authentik apps to blueprints

## The ask

Every Authentik application/provider should be defined by a git **blueprint** (IaC /
disaster-recovery), not made in the UI (DB-only). Most already are
(`configmap-authentik-blueprints.yaml`). This job converts the **8 that are still
UI-made**, matching the live objects exactly so Authentik updates them in place (a
mismatched identifier would create a duplicate or break the app's login).

## Decision (settled with the owner, 2026-11-27)

**Preserve each OIDC app's existing client secret via Key Vault** — read the current
secret from Authentik, store it in Key Vault, reference it in the blueprint with
`!Env`. No app breakage, fully reproducible. (Rejected: rotating to a generated secret
would break each app until its config is updated; structure-only would leave the secret
DB-only.)

## The KV-sourced secret pattern (copy dockhand/netbox/semaphore/stork)

Per OIDC app `<APP>` (worker env var `<APP>_OIDC_CLIENT_SECRET`):
1. **Key Vault** secret `AUTHENTIK-<APP>-OIDC-CLIENT-SECRET` = the current client secret
   (the **owner** adds this; I can't write Key Vault).
2. **`kubernetes/secrets/cluster-secrets.yaml`** — two lines, in a PR of its own that
   merges *before* anything uses it (an undefined `${...}` fails its Kustomization):
   - `data`: `- {secretKey: AUTHENTIK_<APP>_OIDC_CLIENT_SECRET, remoteRef: {key: AUTHENTIK-<APP>-OIDC-CLIENT-SECRET}}`
   - template: `AUTHENTIK_<APP>_OIDC_CLIENT_SECRET: "{{ .AUTHENTIK_<APP>_OIDC_CLIENT_SECRET | b64enc }}"`
3. **`authentik-extras/app/secret-authentik-<app>-oidc.yaml`** — a Secret
   `data: {client-secret: ${AUTHENTIK_<APP>_OIDC_CLIENT_SECRET}}`, `prune: disabled`.
4. **`authentik/authentik/app/values.yaml`** worker env — `<APP>_OIDC_CLIENT_SECRET` from
   secretKeyRef `authentik-<app>-oidc` key `client-secret`; then `generate`.
5. **Blueprint** entry in `configmap-authentik-blueprints.yaml` — `client_secret: !Env
   <APP>_OIDC_CLIENT_SECRET`. A blueprint reading a *new* env fails its first apply and
   isn't retried → re-apply after the worker restarts (restart `deploy/authentik-worker`).

Match identifiers to the live objects: application by `slug`, provider by `name`
(`Provider for <Name>`), groups by `name`. Use `issuer_mode: per_provider`,
`sub_mode: hashed_user_id`, the flows and scope mappings recorded below.

## The 8 apps

### OIDC confidential (need Key Vault; batch after KV + the cluster-secrets PR)

| slug | provider name | client_id | KV secret name | group |
|---|---|---|---|---|
| home-assistant | Provider for Home Assistant | a38Vm… | AUTHENTIK-HOMEASSISTANT-OIDC-CLIENT-SECRET | Home Assistant Users |
| immich | Provider for Immich | XQ42… | AUTHENTIK-IMMICH-OIDC-CLIENT-SECRET | Immich Users |
| vaultwarden | Provider for Vaultwarden | WLBG… | AUTHENTIK-VAULTWARDEN-OIDC-CLIENT-SECRET | Vaultwarden Users |
| babybuddy-mcp | babybuddy-mcp | 5FZn… | AUTHENTIK-BABYBUDDY-MCP-OIDC-CLIENT-SECRET | (none) |

Per-provider detail (all `confidential`, `per_provider`, `hashed_user_id`, signing
`authentik Self-signed Certificate`, invalidation `default-provider-invalidation-flow`):
- **home-assistant**: auth flow **explicit**-consent; scopes openid/email/profile;
  redirect `https://homeassistant${SUBDOMAIN_SUFFIX}.${BASE_DOMAIN}/auth/oidc/callback`.
- **immich**: auth flow implicit-consent; scopes openid/email/profile; redirects (strict)
  `https://immich${SUBDOMAIN_SUFFIX}.${BASE_DOMAIN}/auth/login`, `…/user-settings`,
  `app.immich:///oauth-callback`.
- **vaultwarden**: auth flow implicit-consent; scopes openid/profile/offline_access
  **plus a custom `email` scope mapping** (the Authentik 2025.10 `email_verified`
  workaround) — blueprint that custom mapping too (model
  `authentik_providers_oauth2.scopemapping`, scope_name `email`, an expression returning
  `email` + `email_verified: true`; dump the live one's expression first);
  redirect `https://vaultwarden${SUBDOMAIN_SUFFIX}.${BASE_DOMAIN}/identity/connect/oidc-signin`.
- **babybuddy-mcp**: auth flow implicit-consent; scopes openid/email/profile; redirect
  `https://bb-mcp${SUBDOMAIN_SUFFIX}.${BASE_DOMAIN}/auth/callback`; no policy binding.

### Proxy / LDAP (no client secret → no Key Vault; batch last, carefully)

These need the **outpost assignment** and flows reproduced; a mismatch breaks the
forward-auth / emby login. Dump each live object's full config (incl. which outpost it's
on) before writing.
- **baby-buddy** (ProxyProvider, forward_single, ext `https://bb.<domain>`, auth flow
  implicit-consent; on the embedded proxy outpost). Note babybuddy also has the OIDC
  `babybuddy-mcp` provider above.
- **termix-gate** (ProxyProvider, forward_single, ext `https://termix.<domain>`, group
  Termix Admins).
- **termix-remote-gate** (ProxyProvider, forward_single, ext `https://termix-remote.<domain>`,
  group Termix Admins).
- **emby** (LDAPProvider, base_dn `DC=ldap,DC=goauthentik,DC=io`, bind/search direct, bind
  flow `authentication-ldap`, group Emby Users; on the LDAP outpost). Highest-risk — emby
  authenticates against the LDAP outpost.

## Batches / order

1. **KV (owner)** — add the 4 `AUTHENTIK-*-OIDC-CLIENT-SECRET` secrets.
2. **cluster-secrets PR** — the 4 entries; merge first.
3. **OIDC conversion**, one app verified at a time (immich or vaultwarden first — clearest):
   secret-authentik-<app>-oidc.yaml + worker env + blueprint; after merge, re-apply the
   blueprint (restart the worker) and confirm login still works + no duplicate provider.
4. **Proxy + LDAP** — blueprint baby-buddy, termix-gate, termix-remote-gate, emby last;
   verify forward-auth and emby login after each.

## Done when

All 8 appear as blueprint keys in `configmap-authentik-blueprints.yaml`, each blueprint
instance applies `successful`, every app still logs in, and no duplicate providers/apps
exist in Authentik. Then delete this file to `old-queues/` with a README row.

## How to resume / checks that came back negative

- Live provider configs were extracted 2026-11-27 (this file). Re-dump before writing each
  blueprint in case they changed.
- Verify no duplicate after each apply:
  `ak shell -c "from authentik.providers.oauth2.models import OAuth2Provider as P; print(P.objects.filter(client_id='<id>').count())"` must stay 1.
