"""TERMIX-REMOTE-SOURCES: the sources allowed to reach Termix through the Cloudflare tunnel
(old-queues/QUEUE-termix-remote.md): the active prefixes NetBox tags termix-remote, sorted, as a YAML
flow list ("[a, b]"). Traefik's termix-remote allow-list reads it as TERMIX_REMOTE_SOURCES in
`sourceRange: ${TERMIX_REMOTE_SOURCES}`, which stays a list once Flux fills it in."""


def build(site):
    sources = sorted(p["prefix"] for p in site.usable("prefix")
                     if any(t.get("slug") == "termix-remote" for t in p.get("tags") or []))
    if not sources:
        raise LookupError("no usable prefix is tagged termix-remote")
    return "[" + ", ".join(sources) + "]"
