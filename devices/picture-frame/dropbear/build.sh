#!/usr/bin/env bash
# Builds Dropbear for the picture frame (Android 6, 32-bit ARM) with Android's NDK, so it uses
# Android's own C library: it then finds root without the /etc/passwd Android doesn't have.
# Key-only: password logins are compiled out. Run in a throwaway Debian container, e.g.
#   kubectl run dropbear-build --restart=Never --image=debian:13 --command -- sleep 3600
#   kubectl cp devices/picture-frame/dropbear/build.sh dropbear-build:/build.sh
#   kubectl exec dropbear-build -- bash /build.sh
#   kubectl cp dropbear-build:/b/dropbear-$DROPBEAR_VERSION/dropbear ./dropbear   (and dropbearkey)
# Prints the downloads' SHA-256 to compare with the README's.
set -euo pipefail

DROPBEAR_VERSION="${DROPBEAR_VERSION:-2026.94}"
NDK="${NDK:-android-ndk-r30}"

apt-get -qq update >/dev/null
apt-get -qq install -y curl unzip bzip2 make file >/dev/null
mkdir -p /b && cd /b
curl -fsSLO "https://matt.ucc.asn.au/dropbear/releases/dropbear-$DROPBEAR_VERSION.tar.bz2"
curl -fsSLo ndk.zip "https://dl.google.com/android/repository/$NDK-linux.zip"
sha256sum "dropbear-$DROPBEAR_VERSION.tar.bz2" ndk.zip
unzip -q ndk.zip && rm ndk.zip
TC=$(ls -d /b/android-ndk-*/toolchains/llvm/prebuilt/linux-x86_64)
tar xjf "dropbear-$DROPBEAR_VERSION.tar.bz2" && cd "dropbear-$DROPBEAR_VERSION"

cat > localoptions.h <<'H'
#define DROPBEAR_SVR_PASSWORD_AUTH 0
#define DROPBEAR_SVR_PAM_AUTH 0
#define DROPBEAR_SVR_PUBKEY_AUTH 1
#define DROPBEAR_X11FWD 0
#define DEFAULT_PATH "/system/bin:/system/xbin"
#define DEFAULT_ROOT_PATH "/system/bin:/system/xbin"
H

# API 23 is Android 6. The frame's linker warns about DT_FLAGS_1 from newer NDKs; harmless.
export CC="$TC/bin/armv7a-linux-androideabi23-clang" AR="$TC/bin/llvm-ar" RANLIB="$TC/bin/llvm-ranlib"
./configure --host=armv7a-linux-androideabi --disable-zlib --disable-lastlog --disable-utmp \
  --disable-utmpx --disable-wtmp --disable-wtmpx --disable-pututline --disable-pututxline \
  --disable-syslog >/tmp/configure.log 2>&1 || { tail -30 /tmp/configure.log; exit 1; }
make -j4 PROGRAMS="dropbear dropbearkey" >/tmp/make.log 2>&1 || { grep -i error /tmp/make.log | head -20; exit 1; }
"$TC/bin/llvm-strip" dropbear dropbearkey
file dropbear dropbearkey
