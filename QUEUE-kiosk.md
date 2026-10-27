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
- **Its sign-in: an Authentik user `wall-display`, a member of `Grafana Viewers` only** (the
  Grafana application admits Grafana Admins and Grafana Viewers; membership is the UI's). Made
  by the owner in the UI with a password of its own; the browser keeps the session.
- **The laptop runs `cage` (a one-app Wayland compositor) and Chromium in kiosk mode, as an
  unprivileged user `kiosk`**, signed in automatically on tty1 at boot: never a desktop, never
  the browser as root on a hypervisor. Set up by `ansible/kiosk.yml`, for the NetBox tag
  `kiosk` only, so it never reaches another host.
- **Its settings come from NetBox**, the host's local context: the page URL (it holds the
  domain), the screen's rotation, and the hours the screen is off. Nothing site-specific in git.

## Checklist

- [ ] 1. The `Wall` dashboard (this pull request); read on a 1080p screen in kiosk mode.
- [ ] 2. The owner: the Authentik user `wall-display` in `Grafana Viewers`.
- [ ] 3. `ansible/kiosk.yml`: the `kiosk` user, `cage`, Chromium, tty1 auto-login starting
      `cage chromium --kiosk <url>`, the rotation (`wlr-randr --transform`), a systemd timer
      turning the panel off and on (`wlr-randr --output <panel> --off/--on`); a check mode that
      reports each; the NetBox tag `kiosk` and the host's local context (`kiosk_url`,
      `kiosk_rotation`, `kiosk_screen_off`, `kiosk_screen_on`). A dry run, then the owner's run.
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
