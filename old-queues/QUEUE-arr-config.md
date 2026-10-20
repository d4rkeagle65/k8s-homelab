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
- [x] SABnzbd's clients (2026-10-18): each app's, every field as it had it, its `api_key` from
      `SABNZBD_API_KEY`; every app's download-client test passed (2026-10-19)
- [x] Phase 4 (2026-10-18), captured as each app had it: tags (none deleted), root folders and
      the default delay profile (each a complete list: one not named is deleted), naming,
      media management and UI settings in Sonarr, Radarr, Lidarr and Bookshelf; Lidarr's and
      Bookshelf's "Standard" metadata profiles ("None" is built in); Prowlarr's links to Sonarr,
      Radarr, Lidarr and Bookshelf (keys from their Secrets; Whisparr's stays the UI's) and its
      sync profile. A dry run showed no change but the qBittorrent key; a second, with one
      change planted in each section, showed every one, so each section is compared. A real run
      (2026-10-19) changed only the two download-client keys it writes every run; Prowlarr's
      applications unchanged. Not Configarr's (none of its sections): each
      app's indexer settings (minimum age, retention, RSS interval), import lists,
      notifications, Prowlarr's indexers
- [x] Phase 5, quality (2026-10-19): Sonarr's and Radarr's 60 and 75 custom formats (in
      `customFormatDefinitions`, each by its own `<app>-<name>` id), their scores, quality sizes
      and 8 profiles each; Lidarr's 3 and Bookshelf's 2 profiles (Configarr doesn't manage their
      sizes). The first real run changed only each profile's cutoff score to 1 (all 21 have
      upgrades off, so it's unused), the second nothing; every score, quality and setting read
      back as before. A dry run with a change planted in each section showed every one but an
      `enabled` flip (config.yml's header says why)
- [x] Whisparr v3 (2026-10-19): `whisparr-v3` on v3-3.1.0.2116 beside v2 with its own
      database (nightly backup) and phase 1's settings; Configarr owns its root folder
      (`/synmedia/media/xxx`: v3's folder formats start with `scenes/` or `movies/`), both
      download clients (qBittorrent by the Web UI login, since this build's client takes no API
      key) and its 7 quality profiles (language isn't Configarr's for Whisparr); Prowlarr syncs
      its indexers into it. v2's 4 files went through v3's `import/` folder into its layout.
      v2 retired: Prowlarr's link to it deleted, its release removed, v3 on the `whisparr`
      hostname; its database's removal is in QUEUE.md's Cleanup
- [x] Outside Configarr (the owner, 2026-10-20: all four from git): a sync Job,
      `prod/apps/media/media-sync/`, as `grimmory-sync` is built (a script and a YAML of
      wanted settings in a ConfigMap whose hashed name reruns the Job on a change). Each app
      through its API, every setting read back after writing; a setting not listed stays the
      app's UI's; `DRY_RUN` reports the differences and changes nothing (Configarr's rule).
      Captured 2026-10-20 (the first run must report no change):
      - **Bookshelf:** `/api/v1/config/development` `metadataSource` =
        `http://rreading-glasses.media:8788`.
      - **qBittorrent** (`/api/v2/app/preferences`, keys from the configarr Secret):
        `save_path` `/synmedia/torrents/downloads/complete`, `temp_path_enabled` true,
        `temp_path` `/synmedia/torrents/downloads/temp`, `auto_tmm_enabled` true,
        `torrent_changed_tmm_enabled` true, `save_path_changed_tmm_enabled` false,
        `category_changed_tmm_enabled` false, ratio 1 / seeding 1440 min (`max_ratio_act` 0),
        queueing 3 / 5 / 3; categories (the complete list) books, console, lidarr, movies,
        music, other, radarr, tv, tv-sonarr, tv-whisparr, xxx at `/synmedia/torrents/<name>`,
        prowlarr and software with no path.
      - **SABnzbd** (`mode=get_config`/`set_config`): `download_dir`
        `/synmedia/nzbget/intermediate`, `complete_dir` `/synmedia/nzbget/completed`;
        categories `*` (pp 3, no script) then movies, tv, music, xxx, prowlarr, console,
        software, books, other at `/synmedia/nzbget/<name>` (script Default, priority -100).
      - **Seerr** (`/api/v1/settings/radarr` and `/sonarr`, through its API rather than seeding
        `settings.json`, which Seerr rewrites): Radarr `radarr:7878`, profile 6, folder
        `/synmedia/media/movies`, minimum availability released, default, sync on; Sonarr
        `sonarr:8989`, profile 4, anime profile 10, folders `/synmedia/media/tv`, season folders,
        default, sync on; external URLs `https://<app>.${BASE_DOMAIN}`; API keys from the apps'
        Secrets. Seerr's own key: `SEERR_API_KEY` (in `cluster-secrets`).
      Trials (2026-10-20, temporary Jobs against the live apps): report-only showed every
      section unchanged; with a change planted in each, all seven planted changes showed
      ("would change"/"would create"); an apply round trip on one qBittorrent preference (3 to
      4 and back) wrote and read back. Merged 2026-10-20: its first run, applying, found every
      section of the four apps unchanged

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
