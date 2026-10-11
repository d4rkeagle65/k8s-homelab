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
- [ ] The variables reach the cluster (`cluster-secrets`), then the Job, the blueprint and the
      worker's env var are pushed; the blueprint re-applied once the worker restarts
      (`kubernetes/CLAUDE.md`)
- [ ] The Job's first run in the cluster succeeds, OIDC settings included
- [ ] Login through Authentik works, as an Organizr Admin and as an Organizr User
- [ ] The *arr tabs load inside Organizr (if one refuses framing, make it type 2)
- [ ] Organizr's homepage shows Sonarr, Radarr, Lidarr and qBittorrent data (qBittorrent may
      need its API key, as for Homepage)
- [ ] Emby, SABnzbd and Jellyseerr (Organizr's overseerr settings) once their keys exist

## Resuming

`kubectl -n organizr logs job/organizr-sync` shows the last run; the first unticked box is next.

## Done when

Every box is ticked and this file moves to `old-queues/`.
