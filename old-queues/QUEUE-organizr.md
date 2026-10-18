# Organizr, configured from git

**The ask:** Organizr set up from YAML, with its connections and the Authentik login configured
automatically, like Homepage.

## Settled

- **No YAML of Organizr's own,** so `organizr.yaml` (`prod/services/organizr/organizr/app/sync/`)
  is applied through its API by the `organizr-sync` Job (`organizr-sync.py` beside it), which
  Flux reruns whenever either file changes (the ConfigMap's hash renames it; the Job is `force`).
  Settings, categories and tabs are matched by key or name and read back; ones not listed are
  left alone and reported.
- **First-run setup** is in the same file (`wizard`), used only on an empty volume. The owner ran
  it by hand on 2026-10-10 with the same values, so it's skipped now.
- **Login:** Authentik's OIDC provider (the `organizr` blueprint); Organizr Admins become Admins,
  Organizr Users Users, nobody else is admitted. Straight to Authentik; `#noredirect` on the
  address shows the local form, where the first-run local admin still signs in with its
  password though its account is linked to Authentik: the way in while Authentik is down.
- **The Organizr Users group's sign-in isn't checked** (the owner, 2026-10-18): no one is in it.
- **Tabs:** apps that refuse framing or log in through Authentik open in a new browser tab
  (type 2); the *arr apps open inside Organizr (type 1).

## Checklist

- [x] Organizr deployed (`organizr` namespace, NFS volume, private networks only)
- [x] Sync script and `organizr.yaml`; run from outside the cluster without the OIDC settings:
      17 settings, 4 categories, 22 tabs, read back; a second run changed nothing
- [x] The variables reach the cluster (`cluster-secrets`), then the Job, the blueprint and the
      worker's env var are pushed; the blueprint re-applied once the worker restarts
      (`kubernetes/CLAUDE.md`)
- [x] The Job's run in the cluster succeeds, OIDC settings included (2026-10-16: 34
      settings, 5 categories, 27 tabs verified)
- [x] Login through Authentik works as an Organizr Admin (the owner, 2026-10-18)
- [x] A way in while Authentik is down: the `#noredirect` form signs in the first-run local
      admin with its password (the owner, 2026-10-18), though that account is linked to
      Authentik (`oidcLinkExistingUsers`; its `auth_service` is `oidc::authentik`)
- [x] The *arr tabs load inside Organizr (2026-10-18, checked in the owner's Chrome: Sonarr,
      Radarr, Prowlarr, Lidarr and Bookshelf each load, a lapsed session at the app's own login
      page). They need the pod network in `TrustedNetworks` (`<APP>__SERVER__TRUSTEDNETWORKS`,
      Sonarr's HelmRelease says why); without it a lapsed session's login redirect is
      `http://`, which the browser blocks inside Organizr's https page, leaving a blank frame.
- [x] qBittorrent's tab (type 2) answered 401 "Unauthorized": its CSRF check rejects a request
      whose Referer is another site. Organizr's pages now send `Referrer-Policy: same-origin`
      (`middleware-referrer-same-origin.yaml`), so its new tabs carry no Referer.
- [x] Organizr's homepage shows Sonarr, Radarr, Lidarr and qBittorrent data (the owner,
      2026-10-18; Organizr's logs hold no errors for the four)
- [x] Emby, SABnzbd and Seerr on Organizr's homepage (Seerr through Organizr's overseerr
      settings), 2026-10-18: the sync wrote and read back 45 settings; in the owner's Chrome,
      Emby's recently added, SABnzbd's queue and Seerr's requests show data, with no errors
      for the three in Organizr's log. Emby's now-playing panel shows only while something
      streams.

## Resuming

`kubectl -n organizr logs job/organizr-sync` shows the last run; the first unticked box is next.

## Done when

Every box is ticked and this file moves to `old-queues/`.

## Checked and negative

- **Sonarr's, Radarr's and Prowlarr's blank tabs** (2026-10-18) were none of these, all the
  same for the *arr apps that loaded: X-Frame-Options or CSP headers (none, signed in or out),
  the apps' authentication settings (Forms, required, on all five), Organizr's tab rows or its
  iframe sandbox (`allow-same-origin` and `allow-scripts` present), frame-busting code in the
  apps' UI (none), or the network (each answered Organizr's page in under 50 ms). Each one's
  login page renders in a frame; the cause was the `http://` login redirect above.
- **An API key doesn't sign in to the *arr apps' UI**: `X-Api-Key` on `/` still redirects to
  the login page, so the signed-in page can only be seen in a browser.
