# Working queue: Kubernetes node updates and version upgrades

## The ask

Two Ansible playbooks, run from Semaphore like `proxmox-upgrade.yml`:

1. **Routine OS updates of the six node containers**, one node at a time.
2. **Kubernetes version upgrades**, one minor version per run, with everything that has to move
   with Kubernetes moved too, and checks before each step that the versions it is about to
   install are compatible with each other and with what runs in the cluster.

First target: 1.36. Later versions follow the same way.

## Found (2026-10-06)

- **Nodes:** Debian 12 LXCs, kubeadm 1.33.3 with stacked etcd on the three control planes,
  kube-vip 0.9.2 as static pods, CRI-O `1.33.0~dev-26.1` with crun. kubeadm, kubelet and kubectl
  are held; CRI-O is not.
- **Package sources:** Kubernetes from `pkgs.k8s.io/core:/stable:/v1.33`. CRI-O from
  `pkgs.k8s.io/addons:/cri-o:/prerelease:/main`, a development repository, frozen at that dev
  build; CRI-O's releases now live at
  `download.opensuse.org/repositories/isv:/cri-o:/stable:/v1.XX/deb/` (the `pkgs.k8s.io` stable
  paths answer 403). The control planes also list a Helm apt repository that no longer answers,
  so `apt update` fails there; their package lists date from early 2025.
- **Latest per minor** (2026-10-06): 1.33.13, 1.34.12, 1.35.9, 1.36.5, 1.37.1.
- **Outside git:** Calico 3.30.2 through the Tigera operator 1.38.3 (its Installation `default`
  was set by hand: `nodeAddressAutodetectionV4: {interface: eth0}`), metrics-server 0.7.2 in
  `kube-system`, kube-vip.
- **Drains:** every CloudNativePG primary has a PodDisruptionBudget allowing 0 evictions, so a
  plain drain hangs on a worker holding one. Nine clusters have 2 instances; immich and
  manictime have 1. All use `local-db`, so an evicted instance waits for its node.

## Researched (2026-10-06; sources go into `ansible/k8s-bundles.yml` with each version)

- **etcd gap:** etcd 3.6 needs every 3.5 member on 3.5.32 or later first, but kubeadm 1.33.13
  installs 3.5.24 and kubeadm 1.34 moves to 3.6.5. Bundle 1.33 overrides the etcd image to
  3.5.32-0 or later (`etcd.local.imageTag`).
- **Core per bundle** (kubeadm/kubelet/kubectl; CRI-O from openSUSE): 1.33.13 / 1.33.13,
  1.34.12 / 1.34.15, 1.35.9 / 1.35.10, 1.36.5 / 1.36.7. kubeadm brings etcd, CoreDNS and pause.
- **Calico tested ranges:** 3.30: 1.31-1.35, 3.31: 1.32-1.35, 3.32: 1.34-1.36, 3.33: 1.35-1.37.
  One minor at a time (3.30 -> 3.31 -> 3.32). The `tigera-operator` chart exists for each;
  from 3.32 the CRDs are separate charts, applied before the operator.
- **Add-ons that must move:** cert-manager 1.18 (end of life; one minor at a time to 1.21, which
  covers 1.33-1.36); CloudNativePG operator 1.26 (one minor at a time to 1.30, which covers
  1.34-1.36 and is tested on 1.33; every operator upgrade restarts the database instances);
  metrics-server 0.7.2 -> 0.8.1 (1.31+) -> 0.9.0 (1.34+); kube-vip 0.9.2 -> 1.2.4 (no stated
  range; built against client-go 0.36; EndpointSlices default since 1.0); Flux 2.9.5 -> 2.9.6
  (2.9 supports 1.34-1.36; its own check accepts 1.33).
- **Version-independent** (no `kubeVersion` limit that bites, stable APIs only): MetalLB,
  external-secrets, Traefik, external-dns, local-path-provisioner, nfs-subdir-external-provisioner
  (unmaintained since 2023), cloudflare-tunnel-ingress-controller, redis-ha, authentik, couchdb,
  immich, app-template, emby, manictime-server.
- **kubeadm per step:** 1.34 moves the CRI socket into `/var/lib/kubelet/instance-config.yaml`
  and drops `--container-runtime-endpoint` from `kubeadm-flags.env`. 1.35 removes the kubelet's
  `--pod-infra-container-image` (kubeadm 1.35 strips it from `kubeadm-flags.env`; 1.36 no longer
  does, so check every node during the 1.35 step) and fails on cgroup v1 (the hosts are Proxmox
  9, cgroup v2 only; check anyway). 1.36: no etcd older than 3.6; pause 3.10.2 while CRI-O 1.36
  defaults to 3.10.1, so set CRI-O's `pause_image` to match kubeadm's.
- **API removals 1.34-1.36:** alpha APIs only. In use (2026-10-06): core `v1` Endpoints only, not
  scheduled for removal.
- **Live:** kube-vip mounts `admin.conf` (not `super-admin.conf`) on all three control planes;
  `kubeadm upgrade` doesn't touch its manifest, so the playbook updates it. Kubelets use the
  systemd cgroup driver.

## Decided

- **Bundles, not per-package versions.** `ansible/k8s-bundles.yml` holds one bundle per
  Kubernetes minor: the exact Kubernetes and CRI-O versions, the kube-vip version, and the
  supported version range of every add-on that depends on the Kubernetes version (Calico, Flux,
  cert-manager, CloudNativePG, MetalLB, ...), each with a link to where the range comes from.
  The upgrade playbook takes one variable, `k8s_bundle`, and installs nothing a bundle doesn't
  name. An add-on running in the cluster that is in neither the bundle's ranges nor its list of
  version-independent releases stops the run: unknown is not compatible.
- **Checks before each upgrade step**, all of which stop the run:
  1. the bundle's minor is the cluster's current minor or the next one (kubeadm's one-minor rule);
  2. every kubelet and the API server are within the skew the target allows; CRI-O's minor
     equals the bundle's;
  3. the bundle's exact packages exist in their repositories;
  4. no deprecated API the target removes is in use (the API server's
     `apiserver_requested_deprecated_apis` metric);
  5. every add-on's live version is inside the bundle's range;
  6. `kubeadm upgrade plan` on the first control plane agrees.
- **Calico moves into Flux first** (a HelmRelease of the `tigera-operator` chart adopting what
  runs, the Installation's hand-set fields kept), so it is upgraded by commits like the other
  add-ons and checked against the bundle's range like them.
- **Flux-managed add-ons are upgraded by commits**, never by the playbook. Each step's bundle
  says which versions must already be running; the commits go first.
- **Every node is drained** for its update, control planes one at a time. Before the drain:
  each CloudNativePG primary on the node is switched over to a replica on another node (the
  status patch `kubectl cnpg promote` makes), and a single-instance cluster on it gets
  `nodeMaintenanceWindow: {inProgress: true, reusePVC: true}`, cleared after. The next node waits
  until every database cluster has all its instances ready again.
- **kubectl runs on a control plane other than the node being worked on**, with its
  `/etc/kubernetes/admin.conf`; Semaphore's own ServiceAccount stays read-only.
- **One owner for the node package sources:** `ansible/tasks/k8s-node-repos.yml`, used by both
  playbooks. The routine playbook keeps the node's current minor; only the upgrade playbook
  changes it. The four Kubernetes packages (kubeadm, kubelet, kubectl, cri-o) stay held, and
  only the upgrade playbook moves them, even within a minor: a kubelet newer than the API server
  breaks the skew rule.
- **Nodes are reached over SSH on their management addresses** with Semaphore's key, pinned in
  Semaphore's `known_hosts`.

## Calico into Flux (the plan, for the owner's go-ahead)

Checked 2026-10-06: the `tigera-operator` chart v3.30.2 (the running version), rendered with
values copied from the live Installation, differs from the cluster (`kubectl diff`, a dry run)
only by two empty defaults on the Installation (`imagePullSecrets: []`,
`kubernetesProvider: ""`); the operator Deployment, its RBAC and ServiceAccount, and the
APIServer, Goldmane and Whisker resources match. So Calico becomes a captured Helm release,
the way every other release here was made:

1. After the node update run, with the cluster healthy: label the Namespace
   (`homelab.local/category=system`) and give each running chart object Helm's ownership
   metadata (`meta.helm.sh/release-name` and `-namespace: tigera-operator`, label
   `app.kubernetes.io/managed-by: Helm`): the Deployment, ServiceAccount, both ClusterRoles,
   the ClusterRoleBinding, the RoleBinding, and the Installation, APIServer, Goldmane and
   Whisker named `default`.
2. `helm repo add projectcalico https://docs.tigera.io/calico/charts`, then `helm install
   tigera-operator projectcalico/tigera-operator --version v3.30.2 -n tigera-operator
   --skip-crds -f <values from the live Installation>`; the operator keeps managing its CRDs
   (`manageCRDs: true`, as now).
3. Read back: the release deployed, `calico-node`'s DaemonSet generation and pods unchanged,
   every `tigerastatus` Available, every node Ready.
4. `backup.py all` writes `prod/system/tigera-operator/tigera-operator/` and the
   HelmRepository; review, commit, push; Flux takes the release over.
5. Later upgrades (3.31, 3.32) are version bumps in its `values.yaml`/`release.yaml`; from 3.32
   the CRDs come from separate charts, applied first.

Back out: before step 4, `helm uninstall --keep-history` is NOT safe (its pre-delete hook
removes Calico); instead delete Helm's release Secret (`sh.helm.release.v1.tigera-operator.v1`)
and the ownership metadata, which leaves the running objects as they were.

## Plan

1. **Routine node updates** (`ansible/k8s-node-update.yml`): repos (drop the dead Helm repo,
   CRI-O source to the stable repository of the running minor), apt upgrade with the four held,
   drain, update, reboot the container when anything was upgraded, uncordon, wait.
2. **Calico into Flux.**
3. **Bundles 1.33 (latest patch, CRI-O off the dev build), 1.34, 1.35, 1.36**: research each
   component's supported range; write `ansible/k8s-bundles.yml`.
4. **Upgrade playbook** (`ansible/k8s-upgrade.yml`): the checks, then `kubeadm upgrade` on the
   control planes one by one, then kubelet, kubectl and CRI-O node by node with drains.
5. **Run it**: bundle 1.33, then per minor: commit the add-on upgrades the next bundle needs,
   check run, real run.

## Checklist

- [x] Routine node update playbook written (its database steps tested in check mode against the live cluster: the switchover targets, an unhealthy cluster, a missing replica)
- [x] Its check run clean in Semaphore, and a real run on all six nodes (2026-10-06): every database switched over and back, immich and manictime waited for their node
- [ ] Semaphore's key and the nodes' host keys in place; inventory group for the nodes
- [x] Calico in Flux, 2026-10-07: helm 4 `install --take-ownership` of chart v3.30.2 with the live Installation's values; its server-side apply refused the operator Deployment's `POD_NAME` fieldRef (owned by `kubectl-create`, the same value with `apiVersion: v1` defaulted), so the failed release record was deleted (never `helm uninstall`: its hook removes Calico) and the install rerun with `--force-conflicts`. No Calico pod restarted; the operator Deployment's generation bumped with the same ReplicaSet
- [x] Bundles 1.33-1.36 researched (above)
- [x] `ansible/k8s-bundles.yml` written
- [x] Upgrade playbook written; bundle checks run against the live cluster (1.33 passes; 1.34 refused for etcd, 1.35 for skipping a minor)
- [x] Cluster on bundle 1.33 (2026-10-06): Kubernetes and CRI-O 1.33.13 on every node, etcd 3.5.34, kube-vip v1.2.4; every database and Calico component healthy afterwards
- [x] Add-ons for 1.34, 2026-10-06: Flux 2.9.6; cert-manager 1.18.2 -> 1.19.6 -> 1.20.4 -> 1.21.2
  (all 27 certificates unchanged throughout); CloudNativePG operator 1.26.0 -> 1.27.1 ->
  1.28.1 -> 1.29.1 -> 1.30.1 with in-place instance manager updates (no database pod
  restarted, no switchover; one expected "binaryFileStream ... EOF" error per pod per upgrade,
  as each instance manager replaces itself mid-reply)
- [x] metrics-server into Flux, 2026-10-06 (`prod/system/metrics-server/`, chart 3.13.1, app 0.8.1);
  the old kube-system objects deleted. The 1.34 check then passed against the live cluster.
- [x] 1.34 (2026-10-06): every node 1.34.12 with CRI-O 1.34.15, etcd 3.6.5, CoreDNS 1.12.1;
  all databases healthy and archiving after the drains. The first run stopped when openSUSE's
  server reset a signing-key download (now retried); the rerun skipped the finished control
  planes, as designed
- [x] 1.35 (2026-10-07): every node 1.35.9 with CRI-O 1.35.10, etcd 3.6.6, CoreDNS 1.13.1
- [x] Calico 3.31.7 then 3.32.2 and metrics-server 0.9.0 (2026-10-07), each a version bump in
  git: the operator (manageCRDs) created 3.32's new CRDs itself, calico-apiserver moved to
  calico-system, no node went NotReady during either calico-node rollout
- [ ] 1.36: its check passes against the live cluster once n8n is listed as
  version-independent (2026-10-07)

## Resuming

Read this file, then `git log --oneline -- QUEUE-k8s-upgrade.md ansible`.

## Done when

The cluster runs bundle 1.36 with every add-on inside its ranges, and both playbooks run from
Semaphore.

## Checked and negative

- **kubeadm's SystemVerification preflight fails on these LXC nodes** (2026-10-06, the first
  1.33 run): it loads the `configs` kernel module to read the kernel config, which a container
  can't. The inventory skips it (`kubeadm_ignore_preflight_errors`); the playbook checks cgroup
  v2 itself. That first run stopped there, after installing kubeadm 1.33.13 and writing the etcd
  override on the first control plane, before `upgrade apply`.
- **No official Kubernetes range for kube-vip**, and no statement on whether Calico may skip a
  minor (so it doesn't).
- **`pkgs.k8s.io` has no stable CRI-O repositories any more** (`addons:/cri-o:/stable:/v1.33` to
  `v1.36` answer 403 on 2026-10-06).
