# A wall display: the cluster's status on the convertible laptop's screen

**The ask** (the owner, 2026-10-27): the HP convertible laptop running Proxmox (a standalone hypervisor for now)
is wall-mounted with its screen folded back, and shows a dashboard of the whole homelab's
status, started at boot.

## Settled

- **The page is a Grafana dashboard in git, `Wall`** (`homelab-wall`,
  `kubernetes/prod/system/monitoring/dashboards/app/json/homelab/wall.json`), not a playlist:
  Grafana can't load playlists from files. One 1920x1080 screen, no scrolling, readable from
  across a room: what's wrong first (counts, then the firing alerts), then the hosts, network,
  storage and backups. Opened with `?kiosk&theme=dark`. Alerts leave out `Watchdog` and
  `InfoInhibitor`, which always fire.
- **No sign-in on the wall: Grafana's "Share externally" link to the Wall dashboard** (the
  owner, 2026-10-27), as `kiosk_url`. A sign-in (Authentik) asked again after every reboot
  and would leave an account on the device; the link shows that one dashboard read-only and
  nothing else, and Grafana's route admits only the private networks (not the Cloudflare
  tunnel), so it works only inside the network or over Tailscale. Its token is a secret for
  that dashboard: it lives in the host's NetBox local context, never git, and is paused or
  revoked in Grafana's sharing settings. No `wall-display` user is needed.
- **The laptop runs `cage` (a one-app Wayland compositor) and Chromium in kiosk mode, as an
  unprivileged user `kiosk`**, signed in automatically on tty1 at boot: never a desktop, never
  the browser as root on a hypervisor. Set up by `ansible/kiosk-deploy.yml`, for the NetBox tag
  `kiosk` only, so it never reaches another host.
- **Its settings come from NetBox**, the host's local context: the page URL (it holds the
  domain), the screen's rotation, and the hours the screen is off. Nothing site-specific in git.

## Checklist

- [ ] 1. The `Wall` dashboard (this pull request); read on a 1080p screen in kiosk mode.
- [x] 2. The share link, made by the owner and checked without a sign-in (the page and its
      dashboard, 17 panels), set as the laptop's `kiosk_url` (2026-10-27).
- [ ] 3. `ansible/kiosk-deploy.yml` (#425): the `kiosk` user (video, render, input), `cage`,
      Chromium and `wlr-randr`, tty1 auto-login whose `.bash_profile` starts
      `cage -s -- kiosk-browser` on tty1 only, the rotation, and `kiosk-screen auto` every 5
      minutes from a timer, which sets the panel from the time of day (tested off 19:00 to
      06:59, on at 07:00, in the host's `kiosk_timezone`). The laptop's NetBox local context
      holds the settings (`kiosk_url` from Grafana's Ingress, rotation normal, 19:00 to 07:00
      America/New_York) and it carries the tag `kiosk`, added 2026-10-27. Left: the Semaphore
      template "Deploy: Kiosk" (the owner), a dry run, then the owner's run and a reboot.
- [ ] 5. More on the Wall, one screen still (the owner, 2026-10-27; a second page every 30
      seconds only if it can't fit): smaller count tiles; Tdarr's queues, running Semaphore
      jobs, Immich's counts and storage, Emby streams and Seerr requests, from
      `wall-exporter` (#431: the apps' APIs, read-only, each reported down rather than zero;
      Key Vault `EMBY-API-KEY`, `SEERR-API-KEY`, `WALL-EXPORTER-SEMAPHORE-TOKEN` from a
      Guest, `WALL-EXPORTER-IMMICH-API-KEY` with only `server.statistics`); Pi-hole and the
      Wi-Fi access points (already scraped); the NAS volumes apart, Synology and TerraMaster,
      each with free and total beside its bar (the TerraMaster's from Garage's data volume
      metrics, its SNMP having no storage). The exporter is live (2026-10-27): all five apps
      read (wall_source_up 1). The denser Wall (#432): 30 panels in 24 rows, every query
      run against Prometheus. Left: read on the wall's screen.
- [ ] 4. Signed in once on the laptop as `wall-display`; the screen read back from a photo or
      the owner's word; then the screen-off timer seen to work overnight.

## Resuming

The first unticked box is next. Each step stands on its own: the dashboard is useful in any
browser, and the playbook touches only hosts tagged `kiosk`.

## Done when

The laptop boots straight to the Wall dashboard, signed in as a viewer, turns its screen off
at night and on in the morning, and this file moves to `old-queues/`.

## Checked and negative

- 2026-10-27: the laptop is Proxmox 9 on Debian 13; it has no desktop. The Homelab overview
  dashboard scrolls and reads poorly from a distance, so the wall has its own.
