#!/usr/bin/env python3
"""Entry point for the Kubernetes cluster backup / GitOps-repo generator.

Usage:
    python backup.py all --output ./homelab --context my-cluster
    python backup.py capture --output ./homelab
    python backup.py generate --output ./homelab --dry-run

Run `python backup.py --help` for the full option list.
"""

import sys

from k8s_backup.cli import main

if __name__ == "__main__":
    sys.exit(main())
