"""Namespace labels capture works out from the live cluster, not from a folder.

  homelab.local/data       database: a CloudNativePG Cluster in the namespace
                           files:    PersistentVolumeClaims, no database
                           stateless: neither
  homelab.local/exposure   internet: a host of the namespace's is a Cloudflare tunnel route,
                                     or a tunnel Ingress lives in the namespace itself
                           lan:      a Traefik Ingress, none of them on the tunnel
                           cluster:  no Ingress at all

Each is a function of what the cluster says. When a list it needs couldn't be read, the
answer is None ("not known"), never a default: capture then keeps the label the file already
has rather than writing a guess.
"""

from __future__ import annotations

from .layout import LABEL_PREFIX

DATA_LABEL = LABEL_PREFIX + "data"
EXPOSURE_LABEL = LABEL_PREFIX + "exposure"

DATA_VALUES = ("database", "files", "stateless")
EXPOSURE_VALUES = ("internet", "lan", "cluster")

TUNNEL_CLASS = "cloudflare-tunnel"
TRAEFIK_CLASSES = ("traefik", "traefik-isolated")


def _ns(obj: dict) -> str:
    return (obj.get("metadata") or {}).get("namespace", "")


def _hosts(ingress: dict) -> set[str]:
    return {r.get("host") for r in (ingress.get("spec") or {}).get("rules") or [] if r.get("host")}


def _class(ingress: dict) -> str:
    return (ingress.get("spec") or {}).get("ingressClassName") or ""


def data_class(namespace: str, pvcs: list[dict] | None, clusters: list[dict] | None) -> str | None:
    if pvcs is None or clusters is None:
        return None
    if any(_ns(c) == namespace for c in clusters):
        return "database"
    if any(_ns(p) == namespace for p in pvcs):
        return "files"
    return "stateless"


def exposure(namespace: str, ingresses: list[dict] | None) -> str | None:
    if ingresses is None:
        return None
    tunnel = [i for i in ingresses if _class(i) == TUNNEL_CLASS]
    tunnel_hosts = set().union(*(_hosts(i) for i in tunnel)) if tunnel else set()
    own = [i for i in ingresses if _ns(i) == namespace]
    if any(_class(i) == TUNNEL_CLASS for i in own):
        return "internet"
    traefik = [i for i in own if _class(i) in TRAEFIK_CLASSES]
    if any(_hosts(i) & tunnel_hosts for i in traefik):
        return "internet"
    if traefik:
        return "lan"
    return "cluster"


def namespace_labels(namespace: str, *, pvcs, clusters, ingresses) -> dict[str, str]:
    """The derived labels that could be worked out; a label whose input couldn't be read is
    left out, so the caller keeps whatever value it already has."""
    out = {}
    data = data_class(namespace, pvcs, clusters)
    if data is not None:
        out[DATA_LABEL] = data
    exp = exposure(namespace, ingresses)
    if exp is not None:
        out[EXPOSURE_LABEL] = exp
    return out
