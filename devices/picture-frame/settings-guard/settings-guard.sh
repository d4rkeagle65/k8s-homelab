#!/system/bin/sh
# ImmichFrame settings guard (k8s-homelab devices/picture-frame).
# When the ImmichFrame settings screen is launched - e.g. a stray up/down swipe by a
# child - send BACK to drop straight back to the slideshow. Left/right swipes
# (next/previous photo) stay within MainActivity and are never touched. Event-driven off
# logcat (no polling): it reacts within about a second and never reloads the slideshow.
PKG=com.immichframe.immichframe
TAG=ImmichSettingsGuard
log -t "$TAG" "started"
while true; do
  logcat -b system -b main -v brief 2>/dev/null | while read -r line; do
    case "$line" in
      *"cmp=$PKG/.SettingsActivity"*)
        # Only send BACK when settings is really in front, so we never BACK out of the slideshow.
        if dumpsys activity activities 2>/dev/null | grep -m1 mResumedActivity | grep -q SettingsActivity; then
          input keyevent 4
          log -t "$TAG" "settings opened -> BACK"
        fi
        ;;
    esac
  done
  # logcat ended unexpectedly; pause briefly and re-attach.
  sleep 3
  log -t "$TAG" "logcat watch restarted"
done
