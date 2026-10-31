# The front-door Bluetooth proxy

A WT32-ETH01 (Wireless-Tag's WT32-S1 ESP32 module on a board with 10/100 Ethernet) running
ESPHome as a Bluetooth proxy by the front door, so Home Assistant reaches the door's ULTRALOQ,
which speaks only Bluetooth: U-tec's cloud API doesn't offer that model. It runs on Wi-Fi, since
the nearest Ethernet is on the other side of the concrete slab from the lock.
`door-bt-proxy.yaml` is upstream's
[`wt32/wt32-eth01.yaml`](https://github.com/esphome/bluetooth-proxies/blob/main/wt32/wt32-eth01.yaml)
with Wi-Fi in place of Ethernet and upstream's settings for a Wi-Fi proxy
([`esp32-generic/esp32-generic.yaml`](https://github.com/esphome/bluetooth-proxies/blob/main/esp32-generic/esp32-generic.yaml)),
plus a fixed name, an API encryption key and an OTA password. Its address, MAC and name are in
NetBox and Kea, never here.

## How it is set up

| Part | State |
|---|---|
| Network | Wi-Fi on the IoT network. Its Kea reservation is on the ESP32's base MAC, which Wi-Fi uses, with the hostname `<site>iotesbtp<room>`, so the NetBox sync makes it a `wireless-tag-wt32-eth01` device with the reservation on `wifi0` (`scripts/netbox_seed_iot.yaml`). |
| Firewall | Home Assistant runs on host networking, so it connects from the workers: TCP 6053 (ESPHome's API) is in the rule that lets the workers reach the IoT network. |
| Home Assistant | The ESPHome integration, added by hand by its address. It isn't among `scripts/ha_iot_setup.yaml`'s integrations, because the setup flow asks for the encryption key. Home Assistant knows it by the base MAC, which `wifi0` holds. |
| Bluetooth | Active scanning, with ESPHome's default scan window and 3 connection slots: upstream's settings for a Wi-Fi proxy. |
| Power | 5 V on the 5V and GND pins: the board has no USB port and no PoE. A USB charger through a USB-to-serial adapter's 5V and GND leads does. |

## Secrets

`secrets.yaml` beside the config, which `.gitignore` here keeps out of git:

```yaml
device_name: <its Kea reservation's hostname>
api_encryption_key: "<32 random bytes, base64>"
ota_password: "<random>"
wifi_ssid: "<the IoT network's Wi-Fi name>"
wifi_password: "<its password>"
```

The Wi-Fi name and password are the IoT network's, typed in by hand. The key and the OTA
password live in Key Vault, so any PC can rebuild them. Create them once, from the repo root
in PowerShell:

```powershell
$k = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
./scripts/keyvault-secrets.ps1 ESPHOME_DOOR_BT_PROXY_API_KEY -New -Raw -Value $k; Remove-Variable k
./scripts/keyvault-secrets.ps1 ESPHOME_DOOR_BT_PROXY_OTA_PASSWORD -New -Raw -Generate 32
```

Then write the file:

```powershell
$kv = './scripts/keyvault-secrets.ps1'
@"
device_name: <its Kea reservation's hostname>
api_encryption_key: "$(& $kv ESPHOME_DOOR_BT_PROXY_API_KEY -Raw -Get)"
ota_password: "$(& $kv ESPHOME_DOOR_BT_PROXY_OTA_PASSWORD -Raw -Get)"
wifi_ssid: "<the IoT network's Wi-Fi name>"
wifi_password: "<its password>"
"@ | Set-Content -Encoding utf8 devices/door-bt-proxy/secrets.yaml
```

The script's listing shows both as Synced `no`: nothing in the cluster reads them.

## Flashing it the first time

ESPHome runs on the PC: `pip install esphome` (Python 3.11 or later). Its first build downloads
ESP-IDF, which takes several minutes. The board has no USB port, so the first flash goes
through a USB-to-serial adapter with 3.3 V logic (CP2102, CH340 or FT232):

| Adapter | Board |
|---|---|
| GND | GND |
| 5V | 5V |
| TX | RX0 (GPIO3) |
| RX | TX0 (GPIO1) |
| | IO0 to GND, with a jumper wire |

TX0 and RX0 sit beside IO0. The pins marked RXD and TXD are another serial port (GPIO5 and
GPIO17) and can't flash it. 5 V never goes on 3V3.

1. With IO0 on GND, plug in the adapter: the board starts in download mode.
2. `esphome run devices/door-bt-proxy/door-bt-proxy.yaml`, and pick the adapter's COM port.
3. Take IO0 off GND and power-cycle the board: with IO0 on GND it starts in download mode
   again.

## Putting it on the network

1. Power it at the door: it joins the IoT Wi-Fi and asks DHCP for an address under its name.
   Its lease in Kea gives its MAC.
2. Add its Kea reservation on the IoT network, with the same hostname. The next NetBox sync
   makes its device.
3. Add TCP 6053 to the firewall rule that lets the workers reach the IoT network.
4. In Home Assistant: **Settings > Devices & services > Add integration > ESPHome**, its
   address and port 6053, then the encryption key. It shows up as a Bluetooth adapter.

## Updating it

Over the network, from a PC that reaches the IoT network:
`esphome run devices/door-bt-proxy/door-bt-proxy.yaml --device <address>` (ESPHome's OTA on
TCP 3232, with the OTA password). `esphome logs ... --device <address>` follows its log
through the API.
