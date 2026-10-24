# Tailscale: the tailnet's policy, DNS and alerts from git and NetBox

**The ask** (the owner, 2026-10-23/24): review the tailnet policy that was put together quickly,
reorganise it, and manage it with automation, deriving from NetBox what belongs there, so a
change in the repo or in NetBox updates the tailnet; plus a small amount of alerting.

## Settled

- **OpenTofu, from Semaphore**, as `tofu/cloudflare`: a `tofu/tailscale` module, state in
  Semaphore's `tfstate` database, plans read by the owner before an apply. Not Tailscale's
  GitHub Action: it needs the policy file, with every real address, in this public repo.
- **The structure is git's, the values are NetBox's or Key Vault's.** No subnet, address, host
  name or email in git (CLAUDE.md); the policy names them through NetBox.
- **Access (who may reach what) stays in git**: groups by role, grants, SSH rules and the
  policy's `tests`. NetBox supplies the addresses those rules apply to.
- **NetBox tags, not custom fields** (the owner, 2026-10-24). `netbox_seed.py` defines them;
  the owner applies them in NetBox, as for every other tag:
  - `tailscale-route-home` on a prefix: named in `hosts` as `net-<name>`, auto-approved for
    `tag:subnet-router` (the first Docker VM's Tailscale).
  - `tailscale-route-travel` on a prefix: the same, auto-approved for `tag:travel-router`.
    Two tags rather than one, so the travel router can never be approved for a home subnet.
  - `tailscale-dns` on a DNS zone: split DNS to both Pi-holes (the devices tagged `pihole`),
    and a search path.
  - `tailscale-host` on a device or VM: named in `hosts`, for grants to point at. It installs
    nothing.
- **Names:** `net-mgmt`, `net-prod`, `net-dmz`, `net-iot`, `net-tr` for the networks;
  `tag:subnet-router`, `tag:travel-router`.
- **Least privilege by role** (the owner's intent behind the old `group:users-*` and
  `tag:svc-*`): a group per role (`group:family`), a grant per service it may reach, a test per
  grant. Adding a user is one line in a group.
- **Left out of the tailnet:** the Work laptop, 2.5G and WAN prefixes. the policy's public-production subnet and the
  old zones of a dropped naming scheme go: remnants of a reorganisation that was dropped.
- **The owner's public domain resolves over the tailnet** (`BASE_DOMAIN`'s zone), with the
  production, management and IoT zones. Split DNS today sends every zone to Pi-hole 1 only.
- **Alerts by polling, not webhooks:** Tailscale's webhooks come from its cloud and would need the
  bridge on the internet. An exporter polls the API (read-only OAuth client) for Prometheus;
  the subnet router's own `tailscaled` metrics (`TS_ENABLE_METRICS`) are scraped too.
  Alerts go Alertmanager to Zammad as every other.
- **Two OAuth clients**, in Key Vault since 2026-10-24: `TAILSCALE-OPENTOFU-OAUTH-CLIENT-ID` and
  `-SECRET` (write: policy file, DNS, devices core and routes, feature settings, maybe auth
  keys), `TAILSCALE-EXPORTER-OAUTH-CLIENT-ID` and `-SECRET` (read only). Neither expires.
  `TAILSCALE_API_KEY` stays Homepage's.
- **Devices are added and removed by hand.** The automation reports stale ones, never deletes.

## Checklist

- [x] 1. NetBox: the four tags, defined by `netbox_seed.py` (#360; `tailscale-host` on IP
      addresses too, #362) and applied 2026-10-24 with the owner's approval of the lists:
      `tailscale-route-home` on Management, Production, DMZ and IoT; `tailscale-route-travel` on
      the travel router's LAN; `tailscale-dns` on the public domain's zone and the production,
      management and IoT zones; `tailscale-host` on both Pi-holes, the first Docker VM
      (Vaultwarden), the travel router and Traefik's LoadBalancer address. Read back: every
      object kept its existing tags.
- [x] 2. The exporter's credentials: not through `cluster-secrets`, which is to retire (the
      owner, 2026-10-24; #361 closed). The `azure-keyvault` store also serves namespaces
      labelled `homelab.local/keyvault: allowed`; the exporter gets a namespace of its own,
      labelled so, with its own ExternalSecret for its two Key Vault secrets (step 6).
- [ ] 3. `tofu/tailscale`, importing today's policy, DNS and settings unchanged (the owner's
      choice, 2026-10-24): the module is in, provider 0.29.2, settings in `main.tf` checked
      field by field against the tailnet, the policy and DNS from `TF_VAR_tailnet`. Left: the
      owner's Semaphore setup (`tofu/README.md`, "The Tailscale module") and the Vaultwarden
      note, then a plan read back as no change, then the apply that records the imports.
- [ ] 4. The reorganisation, its own pull request: the renames, the clean-up below, roles,
      tests, and `hosts`, routes, `autoApprovers`, split DNS and search paths from NetBox.
- [ ] 5. A weekly check-mode plan (drift: console edits, NetBox and the tailnet disagreeing).
- [ ] 6. The exporter, its scrape of `tailscaled`, and the alerts: router offline, key expiring,
      route not approved, device waiting for approval, policy changed outside an apply.
- [ ] 7. Later, the owner's call: a second subnet router on the second Docker VM, failing over;
      the DMZ and the firewall's routes for 100.64.0.0/10 were built for this with the old
      WireGuard LXC and may need changes (a shared DMZ address, keepalived).

**The clean-up for 4** (the review of 2026-10-24):
- the peer-relay grant targets `tag:dev-hsn-exit`, which only the deleted LXC carried: it moves
  to `tag:subnet-router`;
- `autogroup:admin` and `autogroup:owner` to the policy's every-subnet ipset repeat `autogroup:owner` to `*`;
- the travel-router grants are written once by tag and once by subnet;
- the travel LAN's route has no `autoApprovers` entry;
- no `tests`;
- `tag:dev-hsn-exit` and the five `tag:svc-*` tags have no device, and the six
  `group:users-*` groups no grant (rebuilt as roles, above).

## Where things are

- Today's policy, DNS and settings: Tailscale's API (`/api/v2/tailnet/-/acl`, `dns/*`,
  `settings`); read with an OAuth client, never written by hand once the module exists.
- NetBox: prefixes (with `dns_zone`), DNS zones (netbox-dns), devices and VMs, their tags.
- The router: `docker/tailscale/` (its routes and tags come from Key Vault; README on the DMZ
  return path and keys that don't expire).

## Resuming

The first unticked box is next. A half-done step leaves the tailnet as it was: the module
imports before it changes anything, and every change is a plan the owner reads.

## Done when

The tailnet's policy, DNS and settings come only from `tofu/tailscale` and NetBox, a weekly plan
shows no drift, the alerts are in, and this file moves to `old-queues/`.

## Checked and negative

- 2026-10-24: the old WireGuard LXC and a laptop unseen since February were still in the tailnet;
  the owner deleted both.
- 2026-10-24: a laptop on the production network lost the management network with Tailscale on:
  Tailscale's /24 beat the hand-made /24 route by metric, and the reply crossed the firewall
  the request had skipped. Two /25 routes via the LAN gateway fixed it (longer prefix wins at
  home; away, the gateway isn't on a connected network and Tailscale's route takes over).
  Nothing for the module to do.
