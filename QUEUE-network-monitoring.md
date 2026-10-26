# Network monitoring: the core switch and the firewall

**The ask** (the owner, 2026-10-26): log forwarding and monitoring for the firewall (the
Barracuda CloudGen F80) and the core switch (the TP-Link TL-SG3428).

## Settled

- **SNMPv3, authPriv (SHA, AES), on both** (the owner, 2026-10-26), one user: Key Vault's
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

- [ ] 1. The owner: the three Key Vault secrets; the same SNMPv3 user on the switch (SNMP >
      SNMP v3: a group with read access, the user with SHA and AES) and on the firewall
      (Firewall Admin: the SNMP service, v3, the user, the management address; allow the
      cluster nodes' management addresses); each device's syslog to the `alloy-syslog`
      address (Homepage's NAS notes have it; switch: BSD on 1514/UDP; firewall: Syslog
      Streaming, 1514/TCP, the log groups settled above); a description on each switch port
      worth an alert (uplinks, servers, access points): CoreSwitchPortDown watches only
      described ports, and 1 of 33 had one on 2026-10-26.
- [ ] 2. The pull request: the ExternalSecret, SNMPv3 in the exporter, the firewall's
      ScrapeConfig, per-target labels on `up` (every SNMP target's `up` was one series,
      so TerraMasterSNMPDown could never fire), alerts (`homelab.network`), syslog senders
      without a hostname named by reverse DNS. Merged after step 1, or the switch's polls
      and the firewall's fail until it is.
- [ ] 3. Read back: `up{switch="core"}` and `up{job="firewall-snmp"}` are 1; the firewall's
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
