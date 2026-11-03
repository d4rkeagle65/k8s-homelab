"""TAILSCALE-ROUTES: the subnets the home subnet router advertises (docker/tailscale's
TS_ROUTES): the active prefixes NetBox tags tailscale-route-home, sorted, comma-separated.
tofu/tailscale's policy approves routes for the same tag, so one tag in NetBox is both."""


def build(site):
    routes = sorted(p["prefix"] for p in site.usable("prefix")
                    if any(t.get("slug") == "tailscale-route-home" for t in p.get("tags") or []))
    if not routes:
        raise LookupError("no usable prefix is tagged tailscale-route-home")
    return ",".join(routes)
