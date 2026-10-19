#!/usr/bin/env python3
"""Generate the Talos machine configs that join this cluster.

Reads what is particular to the cluster from the same place OpenTofu does,
TF_VAR_site's talos block (the Vaultwarden note "OpenTofu site (Proxmox)", saved
as a file: --site), and the rest from the live cluster (its nodes and API
servers), so nothing internal is kept in git; combines it with the owner's
secrets.yaml (from Vaultwarden, never git) and the patches in talos/patches/,
and writes controlplane.yaml, worker.yaml and talosconfig next to
secrets.yaml, each checked with `talosctl validate`.

Stops, changing nothing, when anything can't be read or doesn't match: the
talosctl version against the installer image's, the API servers' first
service-account issuer against the API address, or the nodes' addresses
against a single network.

The outputs hold the cluster's CA keys: apply them, then delete them.
"""

import argparse
import ipaddress
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCHES = HERE / "patches"
COMMON = PATCHES / "common.yaml"
# Control planes only: the generated encryption document out, before ours
# (built from the secrets and encryption-order.txt) goes in.
ENCRYPTION_DELETE = PATCHES / "controlplane-encryption-delete.yaml"
ENCRYPTION_ORDER = PATCHES / "encryption-order.txt"
# Workers only: the local-db volume on the VM's second disk, and the kubelet's
# memory eviction thresholds.
WORKER_ONLY = [PATCHES / "worker-local-db.yaml", PATCHES / "worker-kubelet-eviction.yaml"]
# Control planes only, in this order: Talos's PodSecurity config out, ours in.
PODSECURITY = [PATCHES / "controlplane-podsecurity-delete.yaml", PATCHES / "controlplane-podsecurity.yaml"]


def fail(message):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def run(argv):
    """Run a command; its stdout, or stop with its stderr."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        fail(f"{argv[0]} isn't installed or isn't on PATH")
    if done.returncode != 0:
        fail(f"{' '.join(argv[:3])} ... failed: {done.stderr.strip()}")
    return done.stdout


def kubectl(context, *args):
    argv = ["kubectl"] + (["--context", context] if context else []) + list(args)
    return run(argv)


def image_version():
    """The Talos version in the installer image line of common.yaml."""
    m = re.search(r"^\s*image:\s*\S+:(v\d+\.\d+\.\d+)\s*$", COMMON.read_text(encoding="utf-8"), re.M)
    if not m:
        fail(f"no installer image with a version found in {COMMON}")
    return m.group(1)


# The cluster's name, as tofu/proxmox/talos-config.tf gives it.
CLUSTER_NAME = "kubernetes"


def cluster_settings(site_text):
    """controlPlaneEndpoint, dnsDomain, the subnets and the name, from TF_VAR_site's talos
    block (the JSON OpenTofu reads; tofu/proxmox/variables.tf describes it)."""
    try:
        talos = json.loads(site_text)["talos"]
    except (ValueError, KeyError, TypeError):
        fail("--site isn't TF_VAR_site's JSON with a talos block")
    names = {"controlPlaneEndpoint": "cluster_endpoint", "dnsDomain": "dns_domain",
             "podSubnet": "pod_subnet", "serviceSubnet": "service_subnet"}
    wanted = {"clusterName": CLUSTER_NAME}
    for key, field in names.items():
        value = str(talos.get(field) or "").strip()
        if not value:
            fail(f"TF_VAR_site's talos block has no {field}")
        wanted[key] = value
    return wanted


def check_issuer(context, endpoint):
    """Every API server's first --service-account-issuer is https://<endpoint>."""
    pods = json.loads(kubectl(context, "-n", "kube-system", "get", "pods", "-l", "component=kube-apiserver",
                              "-o", "json"))["items"]
    if not pods:
        fail("no kube-apiserver pods found")
    want = f"https://{endpoint}"
    for pod in pods:
        issuers = [a.split("=", 1)[1] for a in pod["spec"]["containers"][0]["command"]
                   if a.startswith("--service-account-issuer=")]
        if not issuers or issuers[0] != want:
            fail(f"{pod['spec'].get('nodeName')}'s first service-account issuer isn't {want} "
                 "(QUEUE-talos.md, Phase 1): Talos control planes issue tokens for it")


def node_network(context, prefix):
    """The one network every node's InternalIP is in, at the given prefix length."""
    nodes = json.loads(kubectl(context, "get", "nodes", "-o", "json"))["items"]
    nets = set()
    for node in nodes:
        ips = [a["address"] for a in node["status"].get("addresses", []) if a["type"] == "InternalIP"]
        if len(ips) != 1:
            fail(f"node {node['metadata']['name']} has {len(ips)} InternalIP addresses")
        nets.add(ipaddress.ip_network(f"{ips[0]}/{prefix}", strict=False))
    if len(nets) != 1:
        fail(f"the nodes' addresses are in {len(nets)} /{prefix} networks; give --node-prefix")
    return str(nets.pop()), len(nodes)


def k8s_version(context):
    v = json.loads(kubectl(context, "version", "-o", "json"))["serverVersion"]["gitVersion"]
    return v.lstrip("v")


def site_patch(settings, node_net):
    """The cluster's own values as a Talos patch: kept out of git, written beside the outputs."""
    return "\n".join([
        "# Generated by talos/gen-config.py from the live cluster; not for git.",
        "apiVersion: v1alpha1",
        "kind: KubeNetworkConfig",
        f"dnsDomain: {settings['dnsDomain']}",
        "podSubnets:",
        f"  - {settings['podSubnet']}",
        "serviceSubnets:",
        f"  - {settings['serviceSubnet']}",
        "---",
        "apiVersion: v1alpha1",
        "kind: KubeNodeConfig",
        "nodeIP:",
        "  validSubnets:",
        f"    - {node_net}",
        "",
    ])


def etcd_patch(node_net):
    """etcd's peer and client addresses on the nodes' network (control planes only)."""
    return "\n".join([
        "# Generated by talos/gen-config.py from the live cluster; not for git.",
        "cluster:",
        "  etcd:",
        "    advertisedSubnets:",
        f"      - {node_net}",
        "",
    ])


def encryption_patch(secrets_text):
    """Secrets' encryption at rest, providers in encryption-order.txt's order.

    The same document tofu/proxmox/talos-config.tf builds.
    """
    order = [l.strip() for l in ENCRYPTION_ORDER.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.strip().startswith("#")]
    unknown = [p for p in order if p not in ("identity", "secretbox")]
    if not order or unknown:
        fail(f"{ENCRYPTION_ORDER} must list identity and/or secretbox, not {unknown or 'nothing'}")
    m = re.search(r"^\s+secretboxencryptionsecret:\s*(\S+)\s*$", secrets_text, re.M)
    if "secretbox" in order and not m:
        fail("secrets.yaml has no secretboxencryptionsecret")
    lines = [
        "# Generated by talos/gen-config.py from the secrets; not for git.",
        "apiVersion: v1alpha1",
        "kind: KubeEtcdEncryptionConfig",
        "config:",
        "  apiVersion: apiserver.config.k8s.io/v1",
        "  kind: EncryptionConfiguration",
        "  resources:",
        "    - resources:",
        "        - secrets",
        "      providers:",
    ]
    for p in order:
        if p == "identity":
            lines.append("        - identity: {}")
        else:
            lines += ["        - secretbox:", "            keys:", "              - name: key2",
                      f"                secret: {m.group(1)}"]
    return "\n".join(lines) + "\n", order


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--secrets", required=True, type=Path,
                        help="the secrets.yaml from Vaultwarden (talosctl gen secrets --from-kubernetes-pki)")
    parser.add_argument("--out", type=Path,
                        help="folder for the outputs; default: the folder secrets.yaml is in")
    parser.add_argument("--site", required=True, type=Path,
                        help="TF_VAR_site's JSON (the Vaultwarden note \"OpenTofu site (Proxmox)\"), saved as a file")
    parser.add_argument("--context", help="kubectl context of the cluster; default: the current one")
    parser.add_argument("--node-prefix", type=int, default=24,
                        help="prefix length of the network all nodes' addresses share (default 24)")
    parser.add_argument("--force", action="store_true",
                        help="overwrite outputs already in the output folder")
    args = parser.parse_args()

    if not args.secrets.is_file():
        fail(f"{args.secrets} doesn't exist")
    if not args.site.is_file():
        fail(f"{args.site} doesn't exist")
    out = args.out or args.secrets.resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    targets = [out / n for n in ("controlplane.yaml", "worker.yaml", "talosconfig",
                                 "site-patch.yaml", "site-patch-etcd.yaml", "site-patch-encryption.yaml")]
    if not args.force and any(t.exists() for t in targets):
        fail(f"outputs already exist in {out}; delete them or give --force")
    if not shutil.which("talosctl"):
        fail("talosctl isn't installed or isn't on PATH")

    want = image_version()
    have = run(["talosctl", "version", "--client", "--short"]).strip()
    if want not in have:
        fail(f"talosctl is '{have}', the installer image is {want}: use the same version")

    settings = cluster_settings(args.site.read_text(encoding="utf-8-sig"))
    check_issuer(args.context, settings["controlPlaneEndpoint"])
    node_net, node_count = node_network(args.context, args.node_prefix)
    version = k8s_version(args.context)

    site = out / "site-patch.yaml"
    site.write_text(site_patch(settings, node_net), encoding="utf-8", newline="\n")
    etcd = out / "site-patch-etcd.yaml"
    etcd.write_text(etcd_patch(node_net), encoding="utf-8", newline="\n")
    encryption_text, order = encryption_patch(args.secrets.read_text(encoding="utf-8"))
    encryption = out / "site-patch-encryption.yaml"
    encryption.write_text(encryption_text, encoding="utf-8", newline="\n")

    run(["talosctl", "gen", "config", settings["clusterName"], f"https://{settings['controlPlaneEndpoint']}",
         "--with-secrets", str(args.secrets),
         "--kubernetes-version", version,
         "--talos-version", ".".join(want.split(".")[:2]),
         "--config-patch", f"@{COMMON}",
         "--config-patch", f"@{site}",
         "--config-patch-control-plane", f"@{ENCRYPTION_DELETE}",
         "--config-patch-control-plane", f"@{encryption}",
         *[a for p in PODSECURITY for a in ("--config-patch-control-plane", f"@{p}")],
         *[a for p in WORKER_ONLY for a in ("--config-patch-worker", f"@{p}")],
         "--config-patch-control-plane", f"@{etcd}",
         "--output-dir", str(out), "--force"])

    for name in ("controlplane.yaml", "worker.yaml"):
        run(["talosctl", "validate", "--config", str(out / name), "--mode", "cloud", "--strict"])
    for name in ("controlplane.yaml", "worker.yaml"):
        if "kind: KubeFlannelCNIConfig" in (out / name).read_text(encoding="utf-8"):
            fail(f"{name} still has a KubeFlannelCNIConfig document; the patch didn't apply")
    cp = (out / "controlplane.yaml").read_text(encoding="utf-8")
    doc = next((d for d in cp.split("\n---\n") if "kind: KubeEtcdEncryptionConfig" in d), "")
    found = re.findall(r"^\s+- (identity|secretbox|aescbc|aesgcm|kms)", doc, re.M)
    if found != order:
        fail(f"controlplane.yaml's encryption providers are {found}, not {order}: the patch didn't apply")

    print(f"Kubernetes {version}, Talos {want}, {node_count} nodes on {node_net}.")
    print(f"Written and validated in {out}: controlplane.yaml, worker.yaml, talosconfig and the site patches.")
    print("They hold the cluster's CA keys: apply them, then delete them.")


if __name__ == "__main__":
    main()
