"""PIHOLE-SETTINGS: the pihole-FTL settings ansible/pihole-setting.yml sets on every Pi-hole.

misc.dnsmasq_lines holds a local=/<zone>/ line for each active NetBox DNS zone tagged
pihole-local (the Pi-holes answer those themselves, and forward none of them), sorted by name,
then the fixed lines. The other settings are configuration, so they're here, in git: they name
nothing of the site.
"""

FIXED_LINES = ["filter-rr=HTTPS"]
SETTINGS = {"ntp.ipv4.active": False, "ntp.sync.active": False, "ntp.sync.rtc.set": False}


def build(site):
    zones = sorted(z["name"].rstrip(".") for z in site.all("plugins/netbox-dns/zones")
                   if z.get("status") == "active" and any(t.get("slug") == "pihole-local" for t in z.get("tags") or []))
    if not zones:
        raise LookupError("no active NetBox DNS zone is tagged pihole-local")
    return {"misc.dnsmasq_lines": [f"local=/{z}/" for z in zones] + FIXED_LINES, **SETTINGS}
