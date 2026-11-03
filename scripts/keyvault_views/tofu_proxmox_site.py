"""TOFU-PROXMOX-TF-VAR-SITE: OpenTofu's site variable for tofu/proxmox (variables.tf, "site"),
built from NetBox, and from Key Vault for what NetBox doesn't hold.

  networks            each bridge scripts/netbox_seed.yaml gives a VLAN: that VLAN's one prefix
                      (its first three octets and its length), and a gateway where an active DNS
                      record tagged "gateway" has its address in the prefix
  node_dns_servers    the Pi-hole VMs' addresses on the nodes' network (Production), by VM name
  node_search_domain  Key Vault's NODE-SEARCH-DOMAIN
  talos               the active DNS record tagged "kubernetes-api": its name with the API's port
                      (cluster_endpoint) and its address (api_vip); the cluster's domain, pod and
                      service subnets from Key Vault
  vm_login            OWNER-USERNAME and the SSH public keys it accepts, each its own secret
"""
import ipaddress
from pathlib import Path

NODES_NETWORK = "Production"
PIHOLE_ROLE = "Pi-hole"
API_PORT = 6443
SSH_KEYS = ("OWNER-SSH-PUBLIC-KEY", "SEMAPHORE-SHARED-SSH-PUBLIC-KEY")


def bridges():
    """[(bridge, VLAN ID, network name)] for each bridge netbox_seed.yaml gives a VLAN."""
    from ruamel.yaml import YAML
    seed = YAML(typ="safe", pure=True).load((Path(__file__).resolve().parents[1] / "netbox_seed.yaml").read_text(encoding="utf-8"))
    return [(n["bridge"], n["vlan"], n["name"]) for n in seed["networks"] if n.get("vlan") is not None]


def tagged(site, slug):
    return [r for r in site.usable("record") if any(t.get("slug") == slug for t in r.get("tags") or [])
            and r.get("type") == "A"]


def build(site):
    prefixes = site.usable("prefix")
    gateways = tagged(site, "gateway")
    networks, nodes_net = {}, None
    for bridge, vid, name in bridges():
        found = [p for p in prefixes if (p.get("vlan") or {}).get("vid") == vid]
        if len(found) != 1:
            raise LookupError(f"{bridge}: {len(found)} usable prefixes on VLAN {vid}, not one")
        net = ipaddress.ip_network(found[0]["prefix"])
        entry = {"prefix": ".".join(str(net.network_address).split(".")[:3]), "prefix_length": net.prefixlen}
        inside = [g for g in gateways if ipaddress.ip_address(g["value"]) in net]
        if len(inside) > 1:
            raise LookupError(f"{bridge}: {len(inside)} gateway records in its prefix, not one")
        if inside:
            entry["gateway"] = inside[0]["value"]
        networks[bridge] = entry
        if name == NODES_NETWORK:
            nodes_net = net
    if nodes_net is None:
        raise LookupError(f"netbox_seed.yaml names no {NODES_NETWORK} network")
    piholes = {v["id"]: v["name"] for v in site.all("virtualization/virtual-machines")
               if (v.get("role") or {}).get("name") == PIHOLE_ROLE and v.get("status", {}).get("value") in ("staged", "active")}
    servers = sorted((piholes[vm], str(ipaddress.ip_interface(a["address"]).ip)) for a in site.usable("ip")
                     if (vm := ((a.get("assigned_object") or {}).get("virtual_machine") or {}).get("id")) in piholes
                     and ipaddress.ip_interface(a["address"]).ip in nodes_net)
    if not servers:
        raise LookupError(f"no {PIHOLE_ROLE} VM has an address on {NODES_NETWORK}")
    api = tagged(site, "kubernetes-api")
    if len(api) != 1:
        raise LookupError(f"{len(api)} active A records tagged kubernetes-api, not one")
    return {
        "networks": networks,
        "node_dns_servers": [address for _, address in servers],
        "node_search_domain": site.secret("NODE-SEARCH-DOMAIN").strip(),
        "talos": {
            "cluster_endpoint": f"{api[0]['fqdn'].rstrip('.')}:{API_PORT}",
            "api_vip": api[0]["value"],
            "dns_domain": site.secret("CLUSTER-DOMAIN").strip(),
            "pod_subnet": site.secret("CALICO-POD-CIDR").strip(),
            "service_subnet": site.secret("KUBERNETES-SERVICE-CIDR").strip(),
        },
        "vm_login": {"user": site.secret("OWNER-USERNAME").strip(),
                     "ssh_keys": [site.secret(name).strip() for name in SSH_KEYS]},
    }
