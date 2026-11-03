"""TERMIX-REMOTE-SOURCES: the sources allowed to reach Termix through the Cloudflare tunnel
(QUEUE-termix-remote.md): the active prefixes NetBox tags termix-remote, sorted,
comma-separated, which Traefik's termix-remote allow-list reads as TERMIX_REMOTE_SOURCES."""


def build(site):
    sources = sorted(p["prefix"] for p in site.usable("prefix")
                     if any(t.get("slug") == "termix-remote" for t in p.get("tags") or []))
    if not sources:
        raise LookupError("no usable prefix is tagged termix-remote")
    return ",".join(sources)
