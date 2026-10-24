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

- [ ] 1. Read Termix's docs for the exact settings: which keys `TERMIX_REQUIRE_EXTERNAL_SECRETS`
      demands, the SSO plugin's variables, plugin installation in a container (Session
      Recording, Import and Export), `guacd` (sidecar or the `full` image), data and recording
      paths, the API's host schema and how a key credential is referenced.
- [ ] 2. The owner's Key Vault values (steps given then): Termix's keys, the Authentik client
      secret, the bastion key's private half.
- [ ] 3. Authentik: a blueprint for Termix's OIDC application and its forward-auth provider.
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
