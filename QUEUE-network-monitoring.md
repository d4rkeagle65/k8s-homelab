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
- [ ] 3. Read back. SNMPv3 works on both (2026-10-26): the switch on SHA and DES (#400), the
      firewall once its Local-In ruleset passed UDP 161 from the management network (its
      rule MGMT-SNMP had blocked it, as its own log showed: `{host="F80"} |= "|161|"`); every
      firewall module answers (system, if_mib, ucd_memory, ucd_system_stats, ucd_la_table).
      Syslog: the firewall's arrives (host F80, 1514/TCP), the Synology's on 514/TCP; 514/UDP
      is BSD for the switch since #402. The switch's lines reach Alloy (its log server
      corrected to the alloy-syslog address, 2026-10-26) but fail BSD parsing ("expecting a
      sequence number [col 5]": a digit right after the priority, a numeric date), so
      514/UDP stores them raw (Alloy's experimental `raw` format, #404). Its line, as
      stored: `<date> <time> <name> <code> <message>`, local time, the priority dropped;
      a sign-in logs, a sign-out and a config save don't at its current level. Parsed
      (#405): its name becomes `host`, and `logs="sparse"` keeps it out of HostLogsSilent.
- [ ] 4. Log alerts from real lines (#405, homelab.firewall): definition updates failing
      (three in a day; one at 12:00 2026-10-26 looked transient), the AD group lookup
      failing to sign in, the event store full (373 lines in 6 hours on 2026-10-26: these
      two fire at once until fixed). Firewall silence is HostLogsSilent's. Left: VPN tunnel
      down and failed admin sign-ins on either device, once a real line of each is seen
      (none in the first day); port-scan events (about 16 an hour) aren't alerted.
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
