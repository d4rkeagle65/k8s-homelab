"""Lists the Job templates a LimitRange's defaults would reach, before those defaults change.

A LimitRange fills in a container's missing requests and limits when its pod is created,
so a check of the running pods misses every Job that isn't running: a CronJob between runs,
a finished Job, a Helm hook that exists only during an upgrade. This reads, in each
namespace with a LimitRange of type Container, every CronJob's and Job's template and every
Helm release's hook Jobs (`helm get hooks`), and for each container (init containers too)
names the requests and limits it leaves to the defaults, with the value each gets. With
Prometheus (the default), it adds each template's peak memory over the retention (15 days)
from its pods' `container_memory_working_set_bytes`, and flags a peak at or above 80% of the
memory limit it would run under.

Read only: kubectl and helm against the current context, Prometheus through the API
server's service proxy (kps-prometheus in monitoring). It stops, answering nothing, when a
read fails: a refused read is not an empty namespace. Exit 0 nothing flagged, 1 something
flagged, 2 could not answer.

    python scripts/limitrange_jobs.py [--no-peaks] [--namespace NS ...]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse

from ruamel.yaml import YAML

FIELDS = (("requests", "cpu"), ("requests", "memory"), ("limits", "cpu"), ("limits", "memory"))
PROMETHEUS = "/api/v1/namespaces/monitoring/services/http:kps-prometheus:9090/proxy/api/v1/query"
NEAR = 0.8


class CouldNotAnswer(Exception):
    pass


def run(args: list[str]) -> str:
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise CouldNotAnswer(f"{' '.join(args[:4])}: {r.stderr.strip()[:300]}")
    return r.stdout


def quantity(value: str) -> float:
    """A Kubernetes quantity as a number: bytes for memory, cores for CPU."""
    units = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "k": 1e3, "M": 1e6, "G": 1e9, "m": 1e-3}
    v = str(value)
    for u in sorted(units, key=len, reverse=True):
        if v.endswith(u):
            return float(v[: -len(u)]) * units[u]
    return float(v)


def defaults_by_namespace(limitranges: list[dict]) -> dict[str, dict]:
    """Each namespace's Container defaults: {"requests": {...}, "limits": {...}}."""
    out: dict[str, dict] = {}
    for lr in limitranges:
        for item in (lr.get("spec") or {}).get("limits") or []:
            if item.get("type") != "Container":
                continue
            d = out.setdefault(lr["metadata"]["namespace"], {"requests": {}, "limits": {}})
            d["requests"].update(item.get("defaultRequest") or {})
            d["limits"].update(item.get("default") or {})
    return out


def pod_spec(obj: dict) -> dict:
    """The pod template's spec of a Job or CronJob."""
    spec = obj.get("spec") or {}
    if obj.get("kind") == "CronJob":
        spec = (spec.get("jobTemplate") or {}).get("spec") or {}
    return ((spec.get("template") or {}).get("spec")) or {}


def gaps(obj: dict, defaults: dict) -> list[dict]:
    """One row per container that leaves a field to the defaults."""
    rows = []
    spec = pod_spec(obj)
    for c in (spec.get("initContainers") or []) + (spec.get("containers") or []):
        res = c.get("resources") or {}
        missing = {}
        for kind, name in FIELDS:
            if name not in (res.get(kind) or {}) and name in defaults.get(kind, {}):
                missing[f"{kind}.{name}"] = defaults[kind][name]
        if missing:
            limit = (res.get("limits") or {}).get("memory") or defaults.get("limits", {}).get("memory")
            rows.append({"container": c["name"], "missing": missing, "memory_limit": limit})
    return rows


def owner_prefixes(objects: list[dict]) -> dict[tuple[str, str], str]:
    """(namespace, pod-name prefix) -> the template's label, for matching pods to templates."""
    return {(o["metadata"]["namespace"], o["metadata"]["name"] + "-"): f"{o['kind']}/{o['metadata']['name']}"
            for o in objects}


def peaks(namespaces: list[str]) -> dict[tuple[str, str, str], float]:
    """(namespace, pod, container) -> peak working-set bytes over Prometheus's retention."""
    q = ('max by (namespace, pod, container) (max_over_time(container_memory_working_set_bytes'
         '{namespace=~"%s", container!="", container!="POD"}[15d]))' % "|".join(namespaces))
    out = json.loads(run(["kubectl", "get", "--raw", PROMETHEUS + "?query=" + urllib.parse.quote(q)]))
    if out.get("status") != "success":
        raise CouldNotAnswer(f"Prometheus: {out.get('error', out.get('status'))}")
    return {(r["metric"]["namespace"], r["metric"]["pod"], r["metric"]["container"]): float(r["value"][1])
            for r in out["data"]["result"]}


def hook_jobs(namespaces: set[str]) -> list[dict]:
    """The Jobs in every Helm release's hooks, in the namespaces given."""
    jobs = []
    for rel in json.loads(run(["helm", "list", "-A", "-o", "json"])):
        if rel["namespace"] not in namespaces:
            continue
        text = run(["helm", "get", "hooks", rel["name"], "-n", rel["namespace"]])
        for doc in YAML(typ="safe", pure=True).load_all(text):
            if isinstance(doc, dict) and doc.get("kind") == "Job":
                doc.setdefault("metadata", {}).setdefault("namespace", rel["namespace"])
                doc["kind"] = "Job"
                doc["hook_of"] = rel["name"]
                jobs.append(doc)
    return jobs


def report(objects: list[dict], defaults: dict, peak: dict | None) -> tuple[list[str], int]:
    lines, flagged = [], 0
    prefixes = owner_prefixes(objects)
    for o in sorted(objects, key=lambda o: (o["metadata"]["namespace"], o["kind"], o["metadata"]["name"])):
        ns, name = o["metadata"]["namespace"], o["metadata"]["name"]
        label = f"{ns} {o['kind']}/{name}" + (f" (hook of {o['hook_of']})" if o.get("hook_of") else "")
        for row in gaps(o, defaults[ns]):
            got = ", ".join(f"{k}={v}" for k, v in row["missing"].items())
            note = ""
            if peak is not None:
                # A pod is this template's when its name starts with the template's name and
                # no longer template name claims it (cronjob-x vs cronjob-x-cleanup).
                mine = [b for (pns, pod, cname), b in peak.items()
                        if pns == ns and cname == row["container"] and pod.startswith(name + "-")
                        and max((p for (pn, p) in prefixes if pn == ns and pod.startswith(p)), key=len) == name + "-"]
                if not mine:
                    note = "; no pod in Prometheus's 15 days"
                else:
                    top = max(mine)
                    note = f"; peak {top / 2**20:.0f}Mi"
                    if row["memory_limit"] and top >= NEAR * quantity(row["memory_limit"]):
                        note += f" >= {int(NEAR * 100)}% of its {row['memory_limit']} limit: FLAG"
                        flagged += 1
            lines.append(f"{label} [{row['container']}]: gets {got}{note}")
    return lines, flagged


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--no-peaks", action="store_true", help="skip Prometheus (templates only)")
    ap.add_argument("--namespace", action="append", default=[], help="only these namespaces")
    args = ap.parse_args()
    try:
        defaults = defaults_by_namespace(json.loads(run(["kubectl", "get", "limitranges", "-A", "-o", "json"]))["items"])
        if args.namespace:
            defaults = {ns: d for ns, d in defaults.items() if ns in args.namespace}
        if not defaults:
            print("No namespace has a Container LimitRange (or none of those asked for): nothing to check.")
            return 0
        objects = []
        for kind in ("cronjobs", "jobs"):
            for o in json.loads(run(["kubectl", "get", kind, "-A", "-o", "json"]))["items"]:
                # A CronJob's own Jobs are its template again; the CronJob stands for them.
                owned = any(r.get("kind") == "CronJob" for r in o["metadata"].get("ownerReferences") or [])
                if o["metadata"]["namespace"] in defaults and not owned:
                    o["kind"] = "CronJob" if kind == "cronjobs" else "Job"
                    objects.append(o)
        objects += hook_jobs(set(defaults))
        peak = None if args.no_peaks else peaks(sorted(defaults))
    except CouldNotAnswer as e:
        print(f"COULD NOT ANSWER: {e}", file=sys.stderr)
        return 2
    lines, flagged = report(objects, defaults, peak)
    print(f"{len(defaults)} namespaces with Container defaults; {len(objects)} Job templates read "
          f"(CronJobs, Jobs not owned by one, Helm hook Jobs); {len(lines)} containers leave a field to them"
          + ("" if peak is None else f"; {flagged} flagged (peak memory >= {int(NEAR * 100)}% of the limit)"))
    for line in lines:
        print("  " + line)
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
