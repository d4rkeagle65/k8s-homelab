# Youtarr: YouTube channels downloaded for Emby

The ask (the owner, 2026-10-19): a Sonarr-like app for YouTube, following channels rather
than TVDB entries (TVDB merges, as with Ms Rachel, change a series under Sonarr). Youtarr
was chosen over Pinchflat and ytdl-sub: it's the most Emby-aware (NFO and posters, playlists
mirrored into Emby, Emby's watched status read back for cleanup) and has a web UI.

## Settled

- **Youtarr** (`github.com/DialmasterOrg/Youtarr`), image `dialmaster/youtarr:v1.89.0`
  (2026-10-17) pinned by digest (`sha256:171dd050b4d667a3ec8811b0ee23c7a7d8831efb4d289a356f0f634a02ceb834`);
  versioned tags exist beside `latest`, `dev-latest` and `dev-rc.*`, which aren't used.
- **Its database:** its own MariaDB on mariadb-operator (`QUEUE-mariadb.md`), one instance,
  nightly `PhysicalBackup` to `mariadb-backups/youtarr`; `utf8mb4_unicode_ci` (its docs' requirement),
  `ALL PRIVILEGES ON youtarr.*`, and `maxUserConnections` set (the operator's default, 10,
  was too few for Grimmory). It creates its own schema on start ("automatically run
  migrations against the external database on boot").
- **Namespace `media`**, beside the other media apps, since it mounts the media share
  (`synmedia-nfs`) by `subPath` like them (`kubernetes/shared/media/synmedia-pvc.yaml`).
- **Runs as the media user** (`MEDIA_PUID`/`MEDIA_PGID`), not root: its docs allow any UID
  if the mounted folders are already owned by it, and its bundled yt-dlp updates itself in
  `/opt/yt-dlp` (mode 0777).

## Facts from its docs (v1.89.0)

- Listens on 3011; health check `GET /api/health`.
- Environment: `DB_HOST`, `DB_PORT`, `DB_NAME` (default `youtarr`), `DB_USER`, `DB_PASSWORD`;
  `AUTH_ENABLED`, `AUTH_PRESET_USERNAME`, `AUTH_PRESET_PASSWORD` (both presets skip the
  first-run setup token and "override existing credentials on each restart"); `TRUST_PROXY`
  (default true), `TZ`, `LOG_LEVEL`, `DATA_PATH` (the video folder inside the container).
  Emby's and Jellyfin's settings live in `config.json`, set in its UI.
- Folders to keep: `/usr/src/app/data` (videos), `/app/config` (`config.json`, `logs/`,
  the yt-dlp cache), `/app/jobs`, `/app/server/images`. **Never mount `/app/migrations`**:
  an empty mount "will overwrite the packaged migrations".
- Layout per channel: `<out>/[__Group/]Channel Name/Channel - Title - id/Channel - Title [id].mp4`,
  with `.nfo`, `.jpg` and `.srt` beside it and the channel's `poster.jpg`; a `__Group`
  subfolder per channel separates libraries; playlists in `__playlists__/`.
- Resources: ffmpeg, yt-dlp, Python, Deno and Apprise are in the image; its compose example
  reserves 1g and limits 2g.

- **The owner's choices (2026-10-20):** it writes to `media/youtarr` (its own Emby library);
  its own login (`AUTH_PRESET_*` from Key Vault); the `youtarr` hostname. Sonarr's YouTube
  channel (120 videos in `media/youtube`, named by TVDB with no YouTube IDs, so Youtarr can't
  adopt them) is downloaded fresh by Youtarr; then the series leaves Sonarr and
  `media/youtube` is deleted.

## Checklist

- [x] Its four variables (`YOUTARR_DB_PASSWORD`, `_DB_ROOT_PASSWORD`, `_ADMIN_USERNAME`,
      `_ADMIN_PASSWORD`) in Key Vault (the owner, 2026-10-20), mapped by their own PR; all four
      read back in `cluster-secrets`
- [x] `media/youtarr` made on the share (2026-10-20), 770 and `1028:100` like `youtube`; the
      media user writes there
- [ ] (PR) `prod/apps/media/youtarr/`: its MariaDB (`youtarr-db`), Database, User, Grant and
      PhysicalBackup; the release (app-template, as the other media apps) with its config PVC,
      the share's folder by `subPath`, the health probes on `/api/health`, its ingress; runs as
      the media user; `/app/migrations` never mounted. The MariaDB resources passed a server
      dry run against the operator's webhook
- [ ] Read back: the database, the first backup in Garage, Youtarr up and logged in, a
      channel added and a video downloaded into the folder with its NFO and poster
- [ ] Emby: a library on the folder (the owner), Emby's URL and key in Youtarr's settings
- [ ] Homepage and Organizr tiles
- [ ] Sonarr's YouTube series removed (files kept until Youtarr's copies are in Emby), then
      `media/youtube` deleted

## Resuming

The first unticked box is next.

## Done when

Youtarr downloads a followed channel into Emby's library on its own, its database is backed
up nightly, and this file moves to `old-queues/` with a row in its README.

## Checked and negative

- **Postgres:** Youtarr supports MariaDB or MySQL only (`QUEUE-mariadb.md`).
- **Pinchflat** (SQLite, UI) and **ytdl-sub** (YAML, no UI) were the alternatives; Youtarr
  was chosen for its Emby features.
