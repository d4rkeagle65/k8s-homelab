"""KEA-DB-CLIENTS: the one CIDR Kea's database lets in (kubernetes/prod/services/kea/kea/app/
db.yaml: its pg_hba rule and its Service each take a single CIDR): the smallest prefix holding
both Docker VMs' production addresses, which NetBox's addresses claiming DOCKER-VM-1-PROD-ADDRESS
and DOCKER-VM-2-PROD-ADDRESS give. A wider prefix lets in its other addresses too, so the run
that would make one wider than /30 stops instead."""
import ipaddress

HOSTS = ("DOCKER-VM-1-PROD-ADDRESS", "DOCKER-VM-2-PROD-ADDRESS")
WIDEST = 30


def build(site):
    addresses = []
    for name in HOSTS:
        found = [obj for kind, obj in site.claiming(name) if kind == "ip"]
        if len(found) != 1:
            raise LookupError(f"{name} has {len(found)} usable addresses claiming it, not one")
        addresses.append(ipaddress.ip_address(site.form("ip", found[0])["address"]))
    net = ipaddress.ip_network(addresses[0])
    while not all(a in net for a in addresses):
        net = net.supernet()
    if net.prefixlen < WIDEST:
        raise LookupError(f"the two addresses need a /{net.prefixlen}, wider than /{WIDEST}")
    return str(net)
