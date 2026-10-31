#!/system/bin/sh
# Home HQ watchdog on the picture frame (k8s-homelab devices/picture-frame/). Started at boot by
# its own line in /system/bin/install-recovery.sh. The board's Mali GPU driver can hang with
# Home HQ's RenderThread stuck in it: the process can't be killed, Android can't restart Home HQ,
# and only a reboot clears it. Once a minute, with the screen on, it checks Home HQ:
#   dead or a zombie for DEAD_MIN minutes in a row      -> reboot
#   alive but not in front for AWAY_MIN minutes in a row -> reboot (someone in Settings has that long)
# Never within SETTLE_S seconds of boot, nor within COOLDOWN_S of its last reboot (kept in
# $D/last-reboot, so it holds across boots). DRY_RUN=1 logs "would reboot" instead. It logs to
# logcat (tag HomeHQWatchdog) and $D/watchdog.log, kept short.
D=/data/local/watchdog
PKG=com.wesbos.homehq
DEAD_MIN=${DEAD_MIN:-3}
AWAY_MIN=${AWAY_MIN:-20}
SETTLE_S=${SETTLE_S:-300}
COOLDOWN_S=${COOLDOWN_S:-1800}
INTERVAL_S=${INTERVAL_S:-60}
DRY_RUN=${DRY_RUN:-0}

note() {
    log -t HomeHQWatchdog "$*"
    echo "$(date '+%F %T') $*" >> $D/watchdog.log
    if [ "$(wc -l < $D/watchdog.log)" -gt 200 ]; then
        tail -n 100 $D/watchdog.log > $D/watchdog.log.new && mv $D/watchdog.log.new $D/watchdog.log
    fi
}

# Home HQ's process state: a ps line's state column (S, R, D, Z...), or "none". A zombie's
# name is the kernel's, cut to its last 15 characters: m.wesbos.homehq.
ZOMBIE_NAME=m.wesbos.homehq
homehq_state() {
    line=$(ps | grep " $PKG\$" | head -n 1)
    [ -z "$line" ] && line=$(ps | grep " $ZOMBIE_NAME\$" | head -n 1)
    [ -z "$line" ] && { echo none; return; }
    set -- $line
    # USER PID PPID VSIZE RSS WCHAN PC STATE NAME
    echo "$8"
}

uptime_s() { read up rest < /proc/uptime; echo "${up%%.*}"; }

[ "$(uptime_s)" -lt "$SETTLE_S" ] && sleep $(( SETTLE_S - $(uptime_s) ))
note "started (dead ${DEAD_MIN}m, away ${AWAY_MIN}m, cooldown ${COOLDOWN_S}s, dry run $DRY_RUN)"
dead=0; away=0; last=""
while true; do
    sleep "$INTERVAL_S"
    if ! dumpsys power | grep -q 'mWakefulness=Awake'; then dead=0; away=0; continue; fi
    state=$(homehq_state)
    if [ "$state" = none ] || [ "$state" = Z ]; then
        dead=$((dead + 1)); away=0; now="Home HQ $state"
    elif ! dumpsys activity activities | grep 'mFocusedActivity' | grep -q "$PKG"; then
        away=$((away + 1)); dead=0; now="Home HQ alive, not in front"
    else
        dead=0; away=0; now="ok"
    fi
    [ "$now" != "$last" ] && note "$now"; last=$now
    if [ "$dead" -ge "$DEAD_MIN" ] || [ "$away" -ge "$AWAY_MIN" ]; then
        prev=$(cat $D/last-reboot 2>/dev/null); stamp=$(date +%s)
        if [ -n "$prev" ] && [ $(( stamp - prev )) -lt "$COOLDOWN_S" ]; then
            note "$now for ${dead}${away}m, but the last reboot was $(( stamp - prev ))s ago: waiting"
            continue
        fi
        if [ "$DRY_RUN" = 1 ]; then
            note "would reboot: $now for $((dead + away))m"; dead=0; away=0; continue
        fi
        echo "$stamp" > $D/last-reboot
        note "rebooting: $now for $((dead + away))m"
        sync; reboot
    fi
done
