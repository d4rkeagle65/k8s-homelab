"""Turns Renovate's run report into Prometheus metrics, written as a text file.

Runs in the renovate CronJob after Renovate itself. Reads the report
(reportType file: every package file Renovate found, its dependencies and their
available updates) and writes, atomically:

- renovate_dependency_info{file, manager, package, current, datasource}: one per
  dependency found (1).
- renovate_dependency_update{file, manager, package, current, latest, update_type}:
  one per available update (1).
- renovate_dependencies, renovate_updates, renovate_problems: counts.
- renovate_report_timestamp_seconds: when the report was written, so a run that
  stopped producing one shows as stale rather than as "no updates".

A report that is missing or unreadable fails the run; the old metrics file is
left alone, and its timestamp keeps ageing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def esc(value) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def labels(**kv) -> str:
    return "{" + ",".join(f'{k}="{esc(v)}"' for k, v in kv.items() if v not in (None, "")) + "}"


def convert(report: dict) -> list[str]:
    info, updates, problems = [], [], len(report.get("problems") or [])
    for repo in (report.get("repositories") or {}).values():
        problems += len(repo.get("problems") or [])
        for manager, files in (repo.get("packageFiles") or {}).items():
            for pf in files or []:
                for dep in pf.get("deps") or []:
                    if dep.get("skipReason"):
                        continue
                    base = dict(file=pf.get("packageFile"), manager=manager,
                                package=dep.get("packageName") or dep.get("depName"),
                                current=dep.get("currentValue") or dep.get("currentDigest"))
                    info.append(f"renovate_dependency_info{labels(**base, datasource=dep.get('datasource'))} 1")
                    for up in dep.get("updates") or []:
                        latest = up.get("newValue") or up.get("newVersion") or up.get("newDigest")
                        updates.append(f"renovate_dependency_update"
                                       f"{labels(**base, latest=latest, update_type=up.get('updateType'))} 1")
    out = ["# HELP renovate_dependency_info A dependency Renovate found.",
           "# TYPE renovate_dependency_info gauge", *sorted(set(info)),
           "# HELP renovate_dependency_update An update available for a dependency.",
           "# TYPE renovate_dependency_update gauge", *sorted(set(updates)),
           "# TYPE renovate_dependencies gauge", f"renovate_dependencies {len(set(info))}",
           "# TYPE renovate_updates gauge", f"renovate_updates {len(set(updates))}",
           "# TYPE renovate_problems gauge", f"renovate_problems {problems}"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("report", help="Renovate's report (reportPath)")
    ap.add_argument("output", help="the metrics file to write (replaced atomically)")
    args = ap.parse_args()
    with open(args.report, encoding="utf-8") as f:
        report = json.load(f)
    lines = convert(report)
    lines += ["# TYPE renovate_report_timestamp_seconds gauge",
              f"renovate_report_timestamp_seconds {int(os.path.getmtime(args.report))}"]
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    tmp = args.output + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, args.output)
    print(f"{len(lines)} lines written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
