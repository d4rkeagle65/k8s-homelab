#!/usr/bin/env python3
"""Entry point for the Kubernetes cluster backup / GitOps-repo generator.

Usage, from the repo root (--output defaults to the current directory):
    python scripts/backup.py all --context my-cluster
    python scripts/backup.py capture
    python scripts/backup.py generate --dry-run

Run `python scripts/backup.py --help` for the full option list.
"""

import sys

from k8s_backup.cli import main

if __name__ == "__main__":
    sys.exit(main())
