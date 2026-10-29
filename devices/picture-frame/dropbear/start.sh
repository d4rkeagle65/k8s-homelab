#!/system/bin/sh
# Dropbear SSH on the picture frame (k8s-homelab devices/picture-frame/). Started at boot by
# its own line in /system/bin/install-recovery.sh, before the boot fixes' hook. Key-only
# (password logins are compiled out); the keys allowed are authorized_keys in this folder.
D=/data/local/ssh
[ -s $D/dropbear_ed25519_host_key ] || $D/dropbearkey -t ed25519 -f $D/dropbear_ed25519_host_key >/dev/null 2>&1
[ -f $D/dropbear.pid ] && kill -0 "$(cat $D/dropbear.pid)" 2>/dev/null && exit 0
exec $D/dropbear -r $D/dropbear_ed25519_host_key -D $D -P $D/dropbear.pid -s -g -p 22 </dev/null >/dev/null 2>&1
