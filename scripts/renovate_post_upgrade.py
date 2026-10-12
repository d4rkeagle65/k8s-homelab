"""Renovate's post-upgrade task: keep captured releases in step, then run generate.

Renovate updates a captured release through its generated files: the chart
version in app/helmrelease.yaml (its flux manager) and the image tags in
app/values.yaml (helm-values). generate rebuilds helmrelease.yaml from
release.yaml's chartVersion and from values.yaml, so a chart update has to
reach release.yaml first or generate would put the old version back:

1. For each release.yaml under kubernetes/prod/, read the chart version from the
   release's app/helmrelease.yaml; when it differs from chartVersion, write it
   into release.yaml (the one hand edit that file expects).
2. Run `backup.py generate` from the repo root.

Allowed by name in Renovate's global config (allowedCommands), and run from the
root of Renovate's checkout. --deps is the folder pip installed generate's
packages into (scripts/requirements.txt), put on generate's PYTHONPATH.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

CHART_VERSION = re.compile(r"^chartVersion:[ \t]*(\S+)[ \t]*$", re.M)
CHART_KEY = re.compile(r"^[ \t]+chart:[ \t]*$", re.M)
VERSION_KEY = re.compile(r"^[ \t]+version:[ \t]*['\"]?([^'\"\s]+)['\"]?[ \t]*$", re.M)


def helmrelease_chart_version(text: str) -> str | None:
    """The chart version under spec.chart in a HelmRelease, or None."""
    chart = CHART_KEY.search(text)
    if not chart:
        return None
    version = VERSION_KEY.search(text, chart.end())
    return version.group(1) if version else None


def sync_chart_versions(root: Path) -> list[str]:
    """Copy each generated HelmRelease's chart version into its release.yaml.

    Returns the release.yaml files changed, as paths relative to root.
    """
    changed = []
    for release in sorted((root / "kubernetes" / "prod").rglob("release.yaml")):
        helmrelease = release.parent / "app" / "helmrelease.yaml"
        if not helmrelease.is_file():
            continue
        text = release.read_text(encoding="utf-8")
        current = CHART_VERSION.search(text)
        wanted = helmrelease_chart_version(helmrelease.read_text(encoding="utf-8"))
        if not current or not wanted or current.group(1) == wanted:
            continue
        text = text[:current.start(1)] + wanted + text[current.end(1):]
        release.write_text(text, encoding="utf-8", newline="\n")
        changed.append(release.relative_to(root).as_posix())
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--deps", help="a folder holding generate's Python packages, put on its PYTHONPATH")
    ap.add_argument("--root", default=".", help="the repository's root (default: the current directory)")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    for path in sync_chart_versions(root):
        print(f"chartVersion updated: {path}")
    env = dict(os.environ)
    if args.deps:
        env["PYTHONPATH"] = os.pathsep.join(p for p in (args.deps, env.get("PYTHONPATH")) if p)
    return subprocess.run([sys.executable, "scripts/backup.py", "generate"], cwd=root, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
