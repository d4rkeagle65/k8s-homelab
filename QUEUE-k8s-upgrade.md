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
- [ ] Its check run clean in Semaphore
- [ ] Semaphore's key and the nodes' host keys in place; inventory group for the nodes
- [ ] Calico in Flux, adopted without a restart of calico-node
- [ ] Bundles 1.33-1.36 researched and written
- [ ] Upgrade playbook written, its checks tested against the live cluster
- [ ] Cluster on 1.33.13 with CRI-O from the stable repository
- [ ] 1.34, 1.35, 1.36

## Resuming

Read this file, then `git log --oneline -- QUEUE-k8s-upgrade.md ansible`.

## Done when

The cluster runs bundle 1.36 with every add-on inside its ranges, and both playbooks run from
Semaphore.

## Checked and negative

- **`pkgs.k8s.io` has no stable CRI-O repositories any more** (`addons:/cri-o:/stable:/v1.33` to
  `v1.36` answer 403 on 2026-10-06).
