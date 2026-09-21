"""Kubernetes cluster backup and GitOps-ready repo generator.

Reads a live cluster via kubectl/helm and writes a Flux-shaped Git repo to
an output directory. Never writes to the cluster.
"""

__version__ = "1.0.0"
