"""PROXMOX-SETTINGS-NTP-SERVE-NETWORKS: the networks the Proxmox hosts' chrony answers time
requests from (ansible/tasks/proxmox-ntp-server.yml's ntp_serve_networks, a list of CIDRs):
the active prefixes NetBox tags ntp-clients, sorted. Serving another network is tagging it."""


def build(site):
    nets = sorted(p["prefix"] for p in site.usable("prefix")
                  if any(t.get("slug") == "ntp-clients" for t in p.get("tags") or []))
    if not nets:
        raise LookupError("no usable prefix is tagged ntp-clients")
    return nets
