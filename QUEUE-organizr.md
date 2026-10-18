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
  address shows the local form for the local admin.
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
- [ ] Login through Authentik works, as an Organizr Admin and as an Organizr User
- [ ] The *arr tabs load inside Organizr (if one refuses framing, make it type 2). Sonarr, Radarr,
      Prowlarr and Lidarr sent a lapsed session to an `http://` login page: they ignored
      Traefik's `X-Forwarded-Proto` (their `TrustedNetworks` was empty), and a browser blocks an
      http navigation inside Organizr's https page, so the frame stayed `about:blank`. Lidarr
      only looked fine because its session was live; Bookshelf already redirects to https.
      Each now trusts the pod network (`<APP>__SERVER__TRUSTEDNETWORKS`, Sonarr's HelmRelease
      says why). Left: after it's deployed, each one's `Location` on `/` reads `https://`, and
      the four tabs load signed out as well as signed in.
- [x] qBittorrent's tab (type 2) answered 401 "Unauthorized": its CSRF check rejects a request
      whose Referer is another site. Organizr's pages now send `Referrer-Policy: same-origin`
      (`middleware-referrer-same-origin.yaml`), so its new tabs carry no Referer.
- [ ] An empty, root-owned `organizr.db` sits in `/config/www/organizr/data/` beside the live
      `hsnOrganizr.db` (made 2026-10-18 by a read that assumed the first-run setup's name).
      Organizr never opens it; remove it, with the owner's go-ahead.
- [ ] Organizr's homepage shows Sonarr, Radarr, Lidarr and qBittorrent data (qBittorrent may
      need its API key, as for Homepage)
- [ ] Emby, SABnzbd and Seerr (Organizr's overseerr settings) once their keys exist

## Resuming

`kubectl -n organizr logs job/organizr-sync` shows the last run; the first unticked box is next.

## Done when

Every box is ticked and this file moves to `old-queues/`.
