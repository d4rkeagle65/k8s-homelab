# The living-room picture frame

An AiMOR 10" Wi-Fi photo frame, rebuilt as a kiosk that shows ImmichFrame
(`kubernetes/prod/apps/immich/immichframe/`). Underneath it is a PF1007L board (Allwinner A33,
`astar-m86_8723cs`), Android 6.0.1, AiMOR firmware 0.20260113. Everything here comes from
[wesbos/photo-frame-dashboard](https://github.com/wesbos/photo-frame-dashboard) ("upstream"),
whose `docs/allwinner-a33-pf1007l.md` is the full guide; this page records what this frame runs
and the order to set it up again. Its address, MAC and name are in NetBox and Kea, never here.

## How it is set up

| Part | State |
|---|---|
| ADB | Over Wi-Fi on port 5555 (`persist.adb.tcp.port=5555`, so it survives reboots). It runs as root with no authentication. |
| Vendor apps | Disabled: `com.efercro.os.aimor` (the stock launcher, which keeps an unencrypted connection a vendor server can run root commands through), `com.DeviceTest`, `com.yhk.qeota` (the vendor updater). |
| WebView | 106.0.5249.126 (the stock 44 can't run ImmichFrame's page). |
| Launchers | Home HQ (`com.wesbos.homehq`, upstream's `dashboard/kiosk`) is the Home app, so it starts at boot; Rootless Launcher stays installed as the way back to Settings. Firefox is installed, unused. |
| Kiosk page | Home HQ's saved URL is ImmichFrame's address. |
| Boot fixes | Upstream's `scripts/pf1007l-boot` (`install.sh apply`): `MAC` (a fixed Wi-Fi MAC, made from the serial number), `NTP_SERVER` (a local time server), `WIFI_DEFER=1`, `POWER_FIX=1` (it runs on a charger above 500 mA), `IGNORE_IPV6_RA=1`, `KIOSK` set to Home HQ, `ADB_ALLOW` empty. |
| Home Assistant | Added by `scripts/ha_iot_setup.py` as an `androidtv` device (its NetBox type, `generic-android-photo-frame`, in `ha_iot_setup.yaml`), from an attended run. |

## Setting it up again

Run every `adb` command from a pod on a worker, not from a PC: the frame then sees the
worker's address, as it does Home Assistant's. A throwaway one, deleted on exit:

```sh
kubectl run adb --rm -it --restart=Never --image=alpine:3.22 -- sh -c "apk add -q android-tools git bash perl-utils && git clone https://github.com/wesbos/photo-frame-dashboard.git /work/pfd && cd /work/pfd && exec bash"
adb connect <frame-address>:5555
```

1. **Root ADB over Wi-Fi.** Upstream's guide, section 1, lists the routes for a frame without
   it. With ADB up, keep it on across reboots:
   `adb shell setprop persist.adb.tcp.port 5555`.
2. **Back up** (upstream's guide, section 4) before changing anything.
3. **A launcher first, then the vendor apps off** (section 5): install Rootless Launcher, check
   Settings is reachable from it, then
   `adb shell pm disable com.efercro.os.aimor`, `com.DeviceTest` and `com.yhk.qeota`. Wait for
   `/data/system/users/0/package-restrictions.xml` to show them `enabled="2"` before rebooting,
   or the change is lost.
4. **WebView 106** (section 6).
5. **Boot fixes:** in `scripts/pf1007l-boot`, copy `frame-boot.conf.example` to
   `frame-boot.conf` and set the values in the table above, then `bash install.sh check`,
   `bash install.sh apply`, reboot, `bash install.sh verify`. Run it with `bash`: Alpine's `sh`
   can't read the script. The fixed MAC is new to the network: put it on the frame's NetBox
   interface and its Kea reservation, or the frame gets a different address.
6. **Home HQ**, built from upstream's `dashboard/kiosk` with this folder's patch (below):
   ```sh
   git apply <this repo>/devices/picture-frame/homehq-swipe-mute.patch
   bash dashboard/kiosk/build.sh
   adb install -r dashboard/kiosk/build/homehq.apk
   adb shell am start -n com.wesbos.homehq/.MainActivity --es url https://<immichframe-address>/
   adb shell am start -n com.wesbos.homehq/.MainActivity --ez home true --ez swipe true
   adb shell input keyevent KEYCODE_HOME
   ```
   Pick Home HQ and **Always** on the frame (Android 6 has no `cmd package set-home-activity`).
   Set the URL with `--es url`: a URL opened as a link (`-a android.intent.action.VIEW -d`) is
   shown but not saved, and the next boot goes back to Home HQ's default page.
7. **The power button as mute** (below).
8. **Home Assistant:** the attended Semaphore run of `ha_iot_setup.py`, with the frame awake.

Check the result:

```sh
adb shell dumpsys activity activities | grep mFocusedActivity   # com.wesbos.homehq/...
adb shell pm list packages -d                                   # the three vendor apps
adb shell getprop persist.adb.tcp.port                          # 5555
```

`adb exec-out screencap` returns a black image on this board even while the panel shows the
page: look at the frame itself.

## Home HQ patch: swipe and mute

`homehq-swipe-mute.patch` applies to upstream's `dashboard/kiosk/src/com/wesbos/homehq/MainActivity.java`
(written against upstream commit `df9f399`):

- **Swipe** (`--ez swipe true`, saved like `menu_button`): a one-finger sideways swipe of at
  least 120 px within 0.8 s sends the page Right (swipe left) or Left (swipe right), which
  ImmichFrame steps with; the tap underneath is cancelled, so one swipe moves one photo. Off by
  default, as a page that scrolls sideways would get both.
- **Mute:** the volume-mute key toggles media's mute, with Android's volume panel shown, and the
  volume keys adjust media rather than the ringer.

If a later upstream change makes it not apply, the patch is small: re-apply its hunks by hand.

## The power button as mute

Android gives the power key to the system, never to an app, so the frame's key layout sends it
as `VOLUME_MUTE` instead. The button is input device `axp22-supplyer` (the AXP223 power chip,
`KEY_POWER`, scan code 116), which uses the shared `Generic.kl`; a layout named after the
device takes its place for that device only.

```sh
adb shell mount -o remount,rw /system
adb shell "echo 'key 116   VOLUME_MUTE   WAKE' > /system/usr/keylayout/axp22-supplyer.kl"
adb shell chmod 644 /system/usr/keylayout/axp22-supplyer.kl
adb shell mount -o remount,ro /system
adb reboot
adb shell dumpsys input | grep -A14 axp22-supplyer | grep KeyLayoutFile   # .../axp22-supplyer.kl
```

The button then no longer turns the screen off, and its long-press power menu is gone. Undo:
remove that file (same remount) and reboot. `WAKE` keeps a press waking the screen if it is off.

## Locked down

- **ADB from the workers only:** set `ADB_ALLOW` in `frame-boot.conf` to the smallest range
  covering the Kubernetes workers' addresses (NetBox), then `install.sh apply`. Pods leave the
  cluster with their worker's address (Calico's `natOutgoing`), so Home Assistant and the pod
  above still reach it. Empty for now.
- **The vendor's remote root shell is closed** by disabling `com.efercro.os.aimor` (upstream's
  `docs/aimor-remote-root-shell.md`); re-enabling it reopens it.
