# Each media app sees only its own folders

The ask (the owner, 2026-10-19): restrict each media app to the folders it works in, so
nothing outside them (`/synmedia/media/_other`, `_duplicates`, the other apps' media, the
share's own top level) is reachable from it.

## Settled

- **The mounts do it, not the apps' settings.** Each app keeps the `synmedia-nfs` claim
  (the NAS's media share) but mounts only its folders, each by `subPath` at the path it
  had inside the container, so no root folder, download path or database row changes.
- **Hardlinks become copies for torrent imports.** A link can't cross two mounts, even on
  one share; on 2026-10-19 five library files were hardlinked to a download, so the cost is
  a seeding torrent's size twice until it's removed. Usenet imports are moves either way.
- **qBittorrent routes each app's downloads to its own folder** (set once through its API,
  2026-10-19, outside git: no OpenTofu provider exists for qBittorrent, and Configarr only
  manages the *arr side). Default torrent management is Automatic (`auto_tmm_enabled`, in
  Manual it ignores category paths); the categories' save paths:
  `books` torrents/books, `radarr` torrents/radarr, `tv-sonarr` torrents/tv-sonarr,
  `lidarr` torrents/lidarr, `tv-whisparr` torrents/tv-whisparr, `xxx` torrents/xxx,
  `tv` torrents/tv, `movies` torrents/movies, `music` torrents/music (all under
  `/synmedia/`). SABnzbd's categories were already one folder each (`nzbget/<category>`).

## Rules

- **Before an app's mounts narrow, nothing it still needs lies outside them:** its download
  queue has no item whose output path is outside the new mounts (move such torrents with
  qBittorrent's automatic management first), and its root folders are all inside.
- **Every folder an app will mount exists first, owned like its neighbours** (`chown
  --reference`): the kubelet creates a missing `subPath` as root, and the apps, running as
  the media user, can't write to it.
- **After the merge:** the app starts, its health page has no new issue, its download
  clients test OK, and a file listing shows only its folders under `/synmedia`.

## Checklist

| App | Folders under `/synmedia` | State |
|---|---|---|
| Bookshelf | media/books, torrents/books, nzbget/books | live and checked (2026-10-19) |
| Sonarr | media/tv, media/youtube, torrents/tv-sonarr, nzbget/tv | live and checked (2026-10-19) |
| Radarr | media/movies, torrents/radarr, nzbget/movies | PR (2026-10-19) |
| Lidarr | media/music, torrents/lidarr, nzbget/music | |
| Whisparr | media/xxx, torrents/xxx, nzbget/xxx | |
| qBittorrent | torrents | |
| SABnzbd | nzbget | |
| Tdarr | media/tv, media/movies | last: it writes into the library |

## Resuming

The first app without a state is next; read its download queue and root folders first
(Rules).

## Done when

Every row is live and checked, and this file moves to `old-queues/`.
