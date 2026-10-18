# The media apps' settings from git

The ask (the owner, 2026-10-18): as much of Sonarr, Radarr, Lidarr, Whisparr, Prowlarr and
Bookshelf as can be set from git, starting with qBittorrent's Web UI API key in each, so a
rotated key is one paste into Vaultwarden.

## Settled

- **Two layers, one owner per setting.** What `config.xml` holds (start-up settings) comes from
  environment variables, `<APP>__<SECTION>__<KEY>`, in each app's HelmRelease: they override the
  file, aren't written back to it, and need no sidecar (the apps rewrite `config.xml`
  themselves, so a generated one would be fought over). Everything in each app's database
  (download clients, root folders, profiles, naming, media management, Prowlarr's indexers and
  apps) comes from **Configarr** (`ghcr.io/raydak-labs/configarr`), a CronJob reading a
  `config.yml` ConfigMap, its secrets through `!env` from Kubernetes Secrets. A setting
  Configarr owns isn't changed in the app's UI; one it doesn't name stays the UI's.
- **The keys `config.xml` takes** (each app's `src/NzbDrone.Core/Configuration/
  ConfigFileProvider.cs`): server (`Port`, `BindAddress`, `UrlBase`, `InstanceName`, SSL,
  `AllowedHosts`, `TrustedNetworks`), auth (`ApiKey`, `AuthenticationMethod`,
  `AuthenticationRequired`, `TrustCgnatIpAddresses`), Postgres, logging (`LogLevel`,
  `ConsoleLogLevel`, `LogSql`, `LogRotate`, `LogSizeLimit`, `LogDbEnabled`, syslog,
  `AnalyticsEnabled`), updates (`Branch`, `UpdateAutomatically`, the mechanism), UI (`Theme`).
- **Whisparr moves to v3** (the owner, 2026-10-18). v2 (2.2.0.404, series-based) isn't
  supported by Configarr; v3 is, but it's a different, movie-based app with its own library, so
  the move is a migration: v3 beside v2 with its own database, the library added to it, then v2
  retired. Configarr owns v3 like the others; v2 gets nothing new meanwhile.
- **Quality is captured as it is** (the owner, 2026-10-18): today's quality definitions, profiles
  and custom formats go into `config.yml` unchanged, so nothing grabs differently. TRaSH-Guides'
  templates would be a later, deliberate change.
- **Analytics off** (the owner, 2026-10-18): `LOG__ANALYTICSENABLED=false` on every app.
- **Nothing unmanaged is deleted:** every `delete_unmanaged` stays off.

## Rules

- **Capture before owning.** Each section of `config.yml` starts as what the app has now, read
  through its API, so Configarr's first run changes nothing. Then edit it.
- **Every change is a dry run first:** `DRY_RUN=true` prints Configarr's diff report (field by
  field, the same as a real run's); a real run only after that diff is the intended one.
- **"Applied" means read back:** after a real run, the setting read through the app's API.
- **Secrets only through `${VAR}`s** in Secrets, reaching Configarr as env vars; never in the
  ConfigMap.

## Where things live

- `kubernetes/prod/apps/media/<app>/app/helmrelease.yaml`: each app's env vars.
- `kubernetes/prod/apps/media/configarr/` (to make): the CronJob, `config.yml`, its Secret.
- Vaultwarden: `QBITTORRENT_API_KEY` (to add, the owner); the apps' own keys are already there
  (`MEDIA_*_API_KEY`), except Whisparr's.

## Checklist

- [x] Phase 1, env vars (2026-10-18, read back through each app's API): Sonarr, Radarr, Lidarr,
      Prowlarr and Bookshelf on Forms login, required, `info` logs, their update channel,
      analytics off (Bookshelf has none). Bookshelf takes only a name containing "Readarr", so
      it's "Bookshelf (Readarr)"
- [x] Phase 2 (2026-10-18): `prod/apps/media/configarr/`, hourly at :17; its first run reached
      all five apps and reported no change
- [x] Phase 3 (2026-10-18): Configarr owns each app's qBittorrent client and its key from
      `QBITTORRENT_API_KEY` (`update_password: true`: written on every run, the diff report always
      lists it); its first real run passed every app's download-client test, Lidarr's on the key
- [ ] SABnzbd's clients (2026-10-18, with phase 4's PR): each app's, every field as it had it,
      its `api_key` from `SABNZBD_API_KEY`; a dry run with the real key changed only the two
      keys. Left: the merge, then each app's download-client test
- [ ] Phase 4 (2026-10-18), captured as each app had it: tags (none deleted), root folders and
      the default delay profile (each a complete list: one not named is deleted), naming,
      media management and UI settings in Sonarr, Radarr, Lidarr and Bookshelf; Lidarr's and
      Bookshelf's "Standard" metadata profiles ("None" is built in); Prowlarr's links to Sonarr,
      Radarr, Lidarr and Bookshelf (keys from their Secrets; Whisparr's stays the UI's) and its
      sync profile. A dry run showed no change but the qBittorrent key; a second, with one
      change planted in each section, showed every one, so each section is compared. Left: the
      merge, then a real run's diff the same. Not Configarr's (none of its sections): each
      app's indexer settings (minimum age, retention, RSS interval), import lists,
      notifications, Prowlarr's indexers
- [ ] Phase 5, quality: today's quality definitions, profiles and custom formats captured
      unchanged (Settled)
- [ ] Whisparr v3 (Settled): its own release and database beside v2, its env vars as phase 1's,
      the library added, its download clients and quality from Configarr; then v2 retired
      (its database kept a day, as the PostgreSQL 18 moves did)
- [ ] Outside Configarr, each its own decision: Bookshelf's metadata source (`/api/v1/config/
      development`), qBittorrent's preferences and categories, SABnzbd's categories and folders,
      Seerr's links to Radarr and Sonarr (`settings.json`)

## Resuming

The first unticked box is next. A phase is done when Configarr's dry run shows no diff for
what it owns and each owned setting reads back from the app.

## Done when

Every box is ticked or decided against, and this file moves to `old-queues/`.

## Checked and negative

- **The Terraform providers** (devopsarr): no qBittorrent API-key field yet
  (terraform-provider-radarr issue 338, open 2026-10-05).
- **Buildarr:** no commits since 2024-05.
- **A generated `config.xml`:** the apps rewrite it; env vars do the same without a fight.
- **Profilarr** (2026-10-18, v2.2.0): a web app with its own database, for Sonarr and Radarr
  only, owning quality, custom formats, naming and delay profiles; no download clients. Beside
  Configarr it would be a second owner of quality. Worth it later only for tuning quality
  interactively (release-name tests, profile simulation): then it owns quality for those two
  and Configarr drops that section for them.
