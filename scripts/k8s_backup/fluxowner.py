"""Which live objects were applied by a hand-authored Flux entrypoint, and so
must not be captured back into the generator's tree.

kustomize-controller labels everything it applies with the name/namespace
of the Flux Kustomization that applied it, and those Kustomizations are in
turn labelled by their parent. Walking that chain up to the bootstrap
`flux-system` Kustomization gives each object's entrypoint: the Kustomization
defined in kubernetes/flux/config/ that it ultimately came from.

Only the generator's own entrypoints feed back into capture. Anything else
-- `cluster-shared` (kubernetes/shared/: the static media PV),
`cluster-test` (the kubernetes/test/ overlay), and `flux-system` itself
(Flux's own namespace and RBAC, from gotk-components.yaml) -- is already
in git in a hand-written form. Capturing it would duplicate it under kubernetes/cluster/
(so two Kustomizations fight over one object) or invent prod/<category>/media-test/
releases.
"""

from __future__ import annotations

GENERATOR_ENTRYPOINTS = {"cluster", "cluster-resources", "cluster-meta"}

KS_NAME_LABEL = "kustomize.toolkit.fluxcd.io/name"
KS_NAMESPACE_LABEL = "kustomize.toolkit.fluxcd.io/namespace"


def _applied_by(obj: dict) -> tuple[str | None, str] | None:
    labels = obj.get("metadata", {}).get("labels") or {}
    name = labels.get(KS_NAME_LABEL)
    return (labels.get(KS_NAMESPACE_LABEL), name) if name else None


class FluxOwnership:
    def __init__(self, kustomizations: list[dict]):
        self._parent: dict[tuple, tuple | None] = {}
        for ks in kustomizations:
            meta = ks.get("metadata", {})
            self._parent[(meta.get("namespace"), meta.get("name"))] = _applied_by(ks)

    def entrypoint(self, obj: dict) -> str | None:
        """Name of the top-level Kustomization `obj` came from, or None if
        it wasn't applied by Flux (or the chain can't be resolved).
        """
        key = _applied_by(obj)
        if key is None or key not in self._parent:
            return None  # not Flux-applied, or by a Kustomization we can't see
        chain = [key]
        while True:
            parent = self._parent.get(chain[-1])
            if parent is None or parent in chain:
                break
            chain.append(parent)
        # chain[-1] is the bootstrap root (flux-system, which applies
        # itself); the entrypoint is the Kustomization directly below it.
        # A one-element chain means the root applied the object directly:
        # Flux's own components, from kubernetes/flux/config/flux-system/.
        return chain[-2][1] if len(chain) >= 2 else key[1]

    def foreign_reason(self, obj: dict) -> str | None:
        entry = self.entrypoint(obj)
        if entry is None or entry in GENERATOR_ENTRYPOINTS:
            return None
        return f"applied by Flux Kustomization '{entry}' (hand-written, already in git)"
