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
      `AUTHENTIK-TERMIX-OIDC-CLIENT-SECRET`. The bastion key's public half (not secret; replaced
      2026-10-25 by one without a passphrase, before any host had the first):
      `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICp9AkdMyivnEB8URZhPhSJF/NIB1XoRhmUkt5KRl1pG termix-bastion`.
- [x] 3. Authentik: the `authentik` namespace on Key Vault and Termix's client secret (#372),
      then the `termix.yaml` blueprint (Termix Admins, a forward-auth gate on the outpost, the
      OIDC application) and the worker's variable (#375).
- [ ] 4. The app (#375; #376 gave Node.js CloudNativePG's CA, `ca.crt` only, after the first
      start failed `SELF_SIGNED_CERT_IN_CHAIN`). Live 2026-10-24: both ExternalSecrets synced,
      `termix-db` 2/2, the pod Ready as user 1000 under `restricted`, `/health` ok, an
      unauthenticated request turned to Authentik (302). Plugins activated by default include
      `sso`; `telemetry` and `web-endpoint` (2026-10's unauthenticated-exposure advisory) are
      loaded but off, and stay off. The owner signed in through SSO (2026-10-24, the log's
      `sso/callback` then a login), so password sign-in and self-registration are off
      (`ALLOW_PASSWORD_LOGIN`, `ALLOW_REGISTRATION`); the owner uninstalled the login-security
      plugin. Left: Session Recording activated and its retention set (the owner), then an SSH
      session and its recording read back.
- [ ] 5. The bastion key on everything that takes an SSH login (the owner, 2026-10-24), as the
      account the playbooks already give a login: the owner's named admin account on the Debian
      hosts (`tasks/admin-user.yml`, its authorized_keys now the owner's keys plus
      `bastion_ssh_keys` from `group_vars/all.yml`), root on OpenWrt (`openwrt-aps.yml`, added,
      never removed). Done 2026-10-25 (#377, the key replaced by #379 before any host had
      the first): tasks 1297 (the 9 Debian hosts, each authorized_keys read back as the owner's
      key plus the bastion key) and 1298 (both access points and the travel router, VERIFIED).
      The owner's first SSH session through Termix with it worked (2026-10-25, a host made by
      hand, the key inline). Left: the Synology, through
      `synology-baseline.yml`'s new key step (`tasks/nas-bastion-keys.yml`): a dry run, then
      the owner's real run of template 29. The TerraMaster's was added by hand (2026-10-25);
      `terramaster-baseline.yml` now keeps it, and needs its Semaphore template (the owner); the Windows DC takes a
      password credential in Termix; the Talos nodes have no SSH.
- [x] 6. The NetBox sync job (`cronjob-termix-netbox-sync.yaml`, `sync/termix-netbox-sync.py`),
      hourly, writing since 2026-10-25 (#381–#383): its first run created 14 hosts with the
      `bastion` credential (made by hand in Termix) and read back VERIFIED; the next run, no
      changes. The travel router has no management address in NetBox and is reached by its
      primary address, the right one (the owner, 2026-10-25). Left: the hand-made "Docker
      Vm 1" duplicates the synced Docker VM, the owner's call to delete it; the Synology signs
      in only once step 5 puts the bastion key on it (the TerraMaster has it, by hand,
      2026-10-25).
- [ ] 6b. The core switch and the firewall in Termix (the owner, 2026-10-25: both have the bastion
      key, put on by hand). NetBox tag `bastion-key` (`netbox_seed.py`'s TERMIX_TAGS): the sync
      logs such a device in as its local context's `termix_user`. Left: the seed run that
      defines the tag; then in NetBox, the tag and `termix_user` on the core switch (the
      TP-Link, on the management network already) and the firewall, and the firewall's
      management address, which NetBox lacks (its interface and address from the owner),
      made its primary with a DNS name; then a sync run shows both created.
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
