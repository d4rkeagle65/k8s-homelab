# Bastion: Termix, a web-based way into every host

**The ask** (the owner, 2026-10-24): a web-based bastion, to SSH into each VM and device that
supports it, from the browser. Termix chosen over Guacamole, Warpgate and Sshwifty (the owner:
it also manages Docker, Kubernetes and cloud platforms, which come later and scoped).

## Settled

- **Termix** (github.com/Termix-SSH/Termix, Apache-2.0, image `ghcr.io/termix-ssh/termix`, a
  pinned version Renovate moves). SSH, RDP and VNC (through `guacd`), file transfer, audit log.
- **Guardrails**, because of its record (about 49 advisories by 2026-10, among them a critical
  authentication bypass through a forged `X-Forwarded-For`, fixed 2026-10-08; one lead
  maintainer; only the latest release gets fixes):
  - two sign-ins: Authentik forward-auth in front of it, and its own OIDC behind (the SSO
    plugin, `OIDC_ROLE_MAP`/`OIDC_ADMIN_GROUP` from Authentik groups, re-read at every sign-in);
  - private networks and the tailnet only, never the Cloudflare tunnel;
  - its updates merged promptly: only the latest release is patched;
  - `TERMIX_REQUIRE_EXTERNAL_SECRETS=true`, its keys from Key Vault, never generated into its
    data folder.
- **Its own namespace, opted in to Key Vault** (`homelab.local/keyvault: allowed`), its secrets
  read by its own ExternalSecret, not `cluster-secrets`. No Vaultwarden.
- **Postgres** through CloudNativePG, with the usual Garage backups and a restore test.
- **Hosts from NetBox**, by a sync job through Termix's API (`/host/db/host`, an API key
  `tmx_...`): every device or VM with an SSH service in NetBox, at its primary address. A host
  added in NetBox appears; one removed goes. Nothing about the site in git.
- **A bastion SSH key** (the owner, 2026-10-24): an ed25519 key made for Termix, its private
  half in Key Vault and in Termix's credential store only, its public half installed by the
  hosts' baseline playbooks. One key opens every host it's installed on: installed only where
  the owner says, revocable by removing it from the playbooks and rotating it.
- **Session recording on** (the Session Recording plugin), the recordings on the NAS.
- **Pod Security:** the image starts as root and drops to `node` (gosu, PUID 1000). The
  namespace is `baseline` unless setting the user directly works under `restricted` (checked,
  not assumed).
- **Docker, Kubernetes and cloud management: later, scoped.** Its own step, with the narrowest
  credentials that do what the owner wants (a ServiceAccount with chosen verbs, not
  cluster-admin; Docker through a socket proxy limited to the calls used). The owner decides the
  scope then.

## Checklist

- [x] 1. Termix's docs read (2026-10-24, docs.termix.site and the repo's `docker/`):
      - Port `PORT` 8080; data `DATA_DIR` `/app/data` (the volume, on the NAS: recordings live in
        `plugin-data/session-recording`); `/tmp` writable (nginx), `/app/html` rewritten at each
        start, so no read-only root filesystem. `runAsUser: 1000` skips the entrypoint's root
        step (gosu); `fsGroup: 1000` for the volume. `restricted` to be checked by a dry run.
      - `TERMIX_REQUIRE_EXTERNAL_SECRETS=true` demands `JWT_SECRET` (64+ characters),
        `DATABASE_KEY`, `ENCRYPTION_KEY` (64 hex each) and `INTERNAL_AUTH_TOKEN`; also set
        `GUACAMOLE_ENCRYPTION_KEY` (64 hex), else it changes each start.
      - Postgres: `DATABASE_DIALECT=postgres`, `DATABASE_URL`, `DATABASE_SSL`.
      - Behind Traefik: `TRUSTED_PROXIES` (the pod network), `EXTERNAL_FORCE_HTTPS=true`.
      - Remote desktop: the built-in engine (`REMOTE_DESKTOP_ENGINE=builtin`), no `guacd`.
      - Plugins: no setting installs one; the `-full` image bundles every official plugin
        (Session Recording, SSO, Import and Export), so a pinned `-full` tag is declarative.
        Recording retention is a UI setting (default 30 days).
      - SSO: `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_ISSUER_URL`, `OIDC_AUTHORIZATION_URL`,
        `OIDC_TOKEN_URL`, `OIDC_GROUP_CLAIM`, `OIDC_ADMIN_GROUP`, `OIDC_ROLE_MAP`
        (`group:role,...`), `EXTERNAL_ALLOW_REGISTRATION`.
      - API keys are made in the UI only (Settings, API keys): the sync job's key is the owner's
        step after the deploy. The REST body for hosts isn't documented; the Import and Export
        JSON (hosts, credentials, `credentialAlias`) and the hosts-config file
        (`TERMIX_HOSTS_CONFIG`: `name`, `ip`, `port`, `username`, `credential`, `folder`, `tags`)
        are, and step 6 uses one of them.
      - Health: `GET /health`; allow 60 s to start.
- [x] 2. The owner's Key Vault values (2026-10-24): `TERMIX-ENCRYPTION-KEY`, `-DATABASE-KEY`,
      `-GUACAMOLE-ENCRYPTION-KEY`, `-INTERNAL-AUTH-TOKEN`, `-JWT-SECRET`, `-SSH-PRIVATE-KEY`, and
      `AUTHENTIK-TERMIX-OIDC-CLIENT-SECRET`. The bastion key's public half (not secret):
      `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILX/HfYILM8HpE2j04mWy1v0Z3EQajp1BlW4RK9+naz/ termix-bastion`.
- [ ] 3. Authentik, in two pull requests so the worker never references a missing Secret:
      first the `authentik` namespace opted in to Key Vault (it holds only Authentik) and the
      `authentik-termix-oidc` ExternalSecret; then the blueprints (an OIDC application and a
      forward-auth proxy provider on the outpost, a Termix Admins group), the worker's
      `TERMIX_OIDC_CLIENT_SECRET`, with the app (step 4).
- [ ] 4. The app: namespace, CloudNativePG cluster and its object store, ExternalSecret,
      Deployment, `guacd`, storage for data and recordings, private Ingress with Authentik's
      forward-auth, Homepage card. Read back: sign-in through both, an SSH session, a recording.
- [ ] 5. The bastion key's public half on the hosts: which hosts and which user, the owner's call;
      through the baseline playbooks, check mode first.
- [ ] 6. The NetBox sync job, dry run first, then on a schedule.
- [ ] 7. An alert when a Termix release has waited unmerged more than a few days (Renovate).
- [ ] 8. Later: Docker, Kubernetes and cloud, scoped as above.

## Resuming

The first unticked box is next. Nothing here changes a host until step 5, and step 5 runs in
check mode first.

## Done when

The owner signs in through Authentik, opens an SSH session to any NetBox host with an SSH service,
the session is recorded, the host list follows NetBox, and this file moves to `old-queues/`.

## Checked and negative

- Guacamole: full browser client and mature, but no Docker/Kubernetes management; Warpgate: an
  SSH proxy without a browser terminal; Sshwifty: no sign-in or recording of its own.
