# Network monitoring: the core switch and the firewall

**The ask** (the owner, 2026-10-26): log forwarding and monitoring for the firewall (the
Barracuda CloudGen F80) and the core switch (the TP-Link TL-SG3428).

## Settled

- **SNMPv3, authPriv with SHA, on both** (the owner, 2026-10-26): AES on the firewall, DES
  on the switch (the TL-SG3428 offers no AES; `homelab_v3_des`). One user: Key Vault's
  `SNMP-V3-USERNAME`, `SNMP-V3-AUTH-PASSPHRASE`, `SNMP-V3-PRIV-PASSPHRASE`, read by the
  monitoring namespace's own ExternalSecret (`snmp-exporter/app/externalsecret.yaml`; the
  namespace opts in to Key Vault), never cluster-secrets. The switch leaves SNMPv2c.
- **The firewall is found in NetBox** (the device of role Firewall, its primary address),
  through the Prometheus SD plugin: no address variable (`scrapeconfig-firewall.yaml`).
- **Firewall logs: box, security and VPN, not the per-connection allow log** (the owner,
  2026-10-26): system and service logs, admin logins, IPS and threat events, blocked
  connections, VPN tunnel up and down.
- **Logs go to the existing syslog receiver** (alloy-events, the `alloy-syslog`
  LoadBalancer: 514 RFC 5424, 1514 BSD, TCP and UDP), labelled `source="syslog"`, `host`, `app`.
- **Device settings are the owner's**, made in each device's UI: this repo holds only the
  receiving side.

## Checklist

- [x] 1. The owner set SNMPv3 and syslog on both (2026-10-26); Key Vault's three secrets.
- [x] 2. #394 merged and live 2026-10-26: `snmp-exporter-v3` synced, each target's own `up`
      (the NASes read 1), the firewall found in NetBox, Alloy reloaded.
- [ ] 3. The devices answering (2026-10-26, from the exporter's own errors): the switch says
      "incoming packet is not authentic" (it knows the user; the auth protocol isn't SHA or
      its auth passphrase isn't Key Vault's); the firewall times out with no answer even to
      SNMPv3 discovery, which needs no passphrase (the SNMP service isn't on its management
      address, or its access list lacks the k8s nodes' management addresses, NetBox's
      .24-.26 and .34-.37 on the management network). Its syslog arrives (host F80, about 23
      lines a minute, mostly blocked connections); the switch's doesn't yet. The switch can only
      send to port 514, which is RFC 5424's: its BSD lines fail to parse there, and Alloy has
      no format detection (`raw` is experimental). The syslog Service's
      externalTrafficPolicy was Cluster, so every sender arrived as a k8s node's address.
      With the real source addresses (#401): the Synology holds 514/TCP, the firewall 1514/TCP,
      and the TerraMaster pushes to Loki directly, so 514/UDP takes the switch's BSD format.
      The firewall's SNMP service listens on its management address (the owner) but answers
      no SNMPv3 discovery from a node or from the Docker VM, which the switch answers: its
      box firewall drops UDP 161 before the service sees it.
      Then read back: `up{switch="core"}` and `up{job="firewall-snmp"}` are 1; the firewall's
      modules each return series (drop the ones CloudGen doesn't answer); Loki has lines with
      both devices' `host`.
- [ ] 4. From real lines: Loki alerts for repeated admin login failures and a VPN tunnel
      down, and one for each device's syslog falling silent.
- [ ] 5. Later: CloudGen's own MIB (sessions, tunnels, HA state), which needs the exporter's
      generator and Barracuda's MIB files; a dashboard.

## Resuming

The first unticked box is next. Until the pull request merges nothing changes; until step 1
is done, the alerts that would fire are CoreSwitchSNMPDown and FirewallSNMPDown.

## Done when

Both devices are polled over SNMPv3 with alerts, their logs are in Loki with alerts read
from real lines, and this file moves to `old-queues/`.

## Checked and negative

- 2026-10-26: before this, the switch was polled over SNMPv2c (16 ports up) and sent no
  syslog; the firewall sent neither; nothing alerted on either.
