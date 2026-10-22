#!/bin/sh
# backup.sh serve | run | check | healthy (compose.yaml says what the job does)
#   serve    a backup now, then one every day at BACKUP_AT (UTC, HH:MM)
#   run      one backup
#   check    download the newest archive, open it and check what's inside
#            (changes nothing): docker exec dockhand-backup backup.sh check
#   healthy  the healthcheck: the last backup succeeded within 26 hours
set -eu

SRC="${SRC:-/src/dockhand}"
COMPOSE="${COMPOSE:-/src/compose}"
REMOTE="garage:${BACKUP_BUCKET:-docker-backups}/dockhand"
STATE="${STATE:-/tmp/state}"
PASS="${DOCKHAND_BACKUP_ZIP_PASSWORD:-}"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
fail() { log "FAILED: $*"; exit 1; }

# Blank, 7z would write the archive unencrypted, and it holds every stack's secrets.
need_password() { [ -n "$PASS" ] || fail "DOCKHAND_BACKUP_ZIP_PASSWORD is blank: nothing is written unencrypted"; }

# 7z with the password on its standard input, never its command line, where the host's
# process list would show it: -p with no value has "7z a" read it there, and "7z t" and
# "7z x" ask for it there (7-Zip 24.09, Alpine's; printf is the shell's own, no process).
with_password() { printf '%s\n' "$PASS" | 7z "$@"; }

# The archive's contents, each checked: the database whole, the key the size Dockhand
# writes (without it the database's secrets can't be read).
check_contents() {
    [ "$(sqlite3 "$1/dockhand.db" 'PRAGMA integrity_check')" = ok ] || fail "the database copy fails its integrity check"
    tables=$(sqlite3 "$1/dockhand.db" "SELECT count(*) FROM sqlite_master WHERE type = 'table'")
    [ "$tables" -gt 0 ] || fail "the database copy has no tables"
    [ "$(wc -c < "$1/.encryption_key")" -eq 32 ] || fail "the encryption key isn't 32 bytes"
    [ -s "$1/compose.yaml" ] || fail "Dockhand's compose.yaml is missing"
    log "contents: database ok ($tables tables), encryption key 32 bytes, compose.yaml"
}

run() (
    need_password
    work=$(mktemp -d /tmp/dockhand-backup.XXXXXX)
    trap 'rm -rf "$work"' EXIT
    # SQLite's online backup: a consistent copy while Dockhand writes, its WAL included.
    sqlite3 "$SRC/db/dockhand.db" ".backup '$work/dockhand.db'"
    cp "$SRC/.encryption_key" "$work/.encryption_key"
    cp "$COMPOSE/compose.yaml" "$work/compose.yaml"
    if [ -f "$COMPOSE/.env" ]; then cp "$COMPOSE/.env" "$work/compose.env"; fi
    check_contents "$work"
    name="dockhand.$(date -u +%Y%m%d-%H%M%S).7z"
    # -mhe=on encrypts the file list too.
    (cd "$work" && with_password a -t7z -mhe=on -p "$name" dockhand.db .encryption_key compose.yaml \
        $( [ -f compose.env ] && echo compose.env ) >/dev/null)
    with_password t "$work/$name" >/dev/null || fail "the new archive doesn't open with the password"
    rclone copyto "$work/$name" "$REMOTE/$name"
    uploaded=$(rclone lsf "$REMOTE/$name") || fail "couldn't list $REMOTE to read the upload back"
    printf '%s\n' "$uploaded" | grep -qx "$name" || fail "$name isn't in $REMOTE after the upload"
    rclone delete --min-age "${BACKUP_KEEP_DAYS:-30}d" "$REMOTE"
    mkdir -p "$STATE"
    date -u +%s > "$STATE/last-ok"
    log "backed up to $REMOTE/$name ($(wc -c < "$work/$name") bytes)"
)

check() (
    need_password
    work=$(mktemp -d /tmp/dockhand-check.XXXXXX)
    trap 'rm -rf "$work"' EXIT
    # The listing on its own: through a pipe, a failed one would read as no archive.
    archives=$(rclone lsf --include 'dockhand.*.7z' "$REMOTE") || fail "couldn't list $REMOTE: no answer, not no archive"
    newest=$(printf '%s\n' "$archives" | sort | tail -n 1)
    [ -n "$newest" ] || fail "no archive in $REMOTE"
    rclone copyto "$REMOTE/$newest" "$work/$newest"
    (cd "$work" && with_password x "$newest" >/dev/null) || fail "$newest doesn't open with the password"
    check_contents "$work"
    log "OK: $newest restores ($(printf '%s\n' "$archives" | grep -c .) archives kept)"
)

# Seconds until the next BACKUP_AT, UTC.
until_next() {
    now=$(date -u +%s)
    at=$(date -u -d "$(date -u +%Y-%m-%d) ${BACKUP_AT:-03:45}:00" +%s)
    [ "$at" -gt "$now" ] || at=$((at + 86400))
    echo $((at - now))
}

serve() {
    mkdir -p "$STATE"
    date -u +%s > "$STATE/started"
    while :; do
        # A process of its own, so set -e stops it at the first failed step: inside
        # "run || ...", the shell would ignore set -e and carry on.
        "$0" run || log "the next try is at ${BACKUP_AT:-03:45} UTC"
        sleep "$(until_next)"
    done
}

# Healthy while the last backup is under 26 hours old, or, before the first, while the
# container is.
healthy() {
    last=$(cat "$STATE/last-ok" 2>/dev/null || cat "$STATE/started" 2>/dev/null || echo 0)
    [ $(( $(date -u +%s) - last )) -lt $((26 * 3600)) ]
}

case "${1:-serve}" in
    serve) serve ;;
    run) run ;;
    check) check ;;
    healthy) healthy ;;
    *) echo "usage: backup.sh serve | run | check | healthy" >&2; exit 2 ;;
esac
