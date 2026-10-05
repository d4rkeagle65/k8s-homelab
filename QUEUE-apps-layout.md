# Working queue: the apps layout and labels

## The ask

Group the cluster's apps by kind, with the grouping coming from the cluster itself, and
clean up labels and annotations so `kubectl` can select by them.

## Decided

- **`kubernetes/apps/` becomes `kubernetes/prod/`**, holding three folders: `system`
  (cluster plumbing), `services` (shared services other apps use) and `apps` (what people
  use). An app's path is `kubernetes/prod/<category>/<namespace>/<release>/`.
- **The category is a label on the Namespace**: `homelab.local/category: system | services |
  apps`. capture reads it to choose the folder, so the layout follows the cluster; a
  namespace without it is refused, never filed by guess.
- **Everything in `prod/` carries `homelab.local/env: prod`.**
- **Labels reach every object through `commonMetadata`**, on each app's Flux Kustomization
  (`ks.yaml`) and on its HelmRelease, which also labels what the chart creates. One place per
  app; no object is labelled by hand.
- **Each app's Flux Kustomization keeps its name**, so Flux updates it in place when its path
  moves instead of deleting and recreating it.
- **Mapping:**
  - `system`: cert-manager, claude-code, cloudflare-tunnel, external-dns, external-secrets,
    external-services, local-path-storage, metallb-system, nfs-client, noip-duc, traefik,
    traefik-isolated.
  - `services`: authentik, cnpg-system, mqtt, redis-ha, smtp-relay.
  - `apps`: babybuddy (with babybuddy-mcp, below), emby, homeassistant, immich, manictime,
    mealie, media, obsidian.
- **babybuddy-mcp moves into the `babybuddy` namespace** as a second release there, and its
  namespace goes.
- **A full audit of labels and annotations is part of this job.**

## Labels

Confirmed. Each comes from a source the generator can already read, so none needs upkeep by
hand.

| Label | Values | On | Source |
|---|---|---|---|
| `homelab.local/env` | `prod` (`test` for `kubernetes/test/`) | everything | the top folder |
| `homelab.local/category` | `system`, `services`, `apps` | everything | the Namespace label |
| `app.kubernetes.io/part-of` | the app (`immich`, `babybuddy`) | everything | the namespace, or a release's override |
| `homelab.local/managed-by` | `helm`, `hand`, `promote` | everything | the release folder's marker |
| `homelab.local/exposure` | `internet`, `lan`, `cluster` | Ingresses, Services | whether a tunnel route or a Traefik route exists |
| `homelab.local/login` | `authentik-oidc`, `authentik-proxy`, `app`, `none` | Ingresses | the route's middleware, or the app |
| `homelab.local/data` | `stateless`, `files`, `database` | Namespaces | whether it has PVCs or a CNPG `Cluster` |

## Plan

1. **Audit.** List every label and annotation key on the live objects, by kind and
   namespace: which are ours, which are tools' (Helm, Flux, kubectl), and which are stale.
   Record the findings here.
2. **Generator.** Teach capture and generate the `prod/<category>/` level and the
   `commonMetadata` labels; make the structure tests and `ownership.py`'s patterns follow.
   Prove it on a copy of the repo first: generate must move every file and change nothing
   else.
3. **Labels on the Namespaces** in the cluster, then one commit moves every app and renames
   `apps/` to `prod/` (the `cluster` Flux Kustomization's path changes with it).
4. **babybuddy-mcp into `babybuddy`**: its Deployment, Service, Secret and Ingress, its
   tunnel route and its Pi-hole record; then delete the empty namespace by hand.
5. **Annotation cleanup** from the audit's findings.

## Audit findings

727 objects in the app namespaces, read live. Most keys belong to tools and stay as they
are: Flux (`kustomize.toolkit.fluxcd.io/*`, `helm.toolkit.fluxcd.io/*`,
`reconcile.fluxcd.io/requestedAt`), Helm (`meta.helm.sh/*`, `helm.sh/chart`, its release
Secrets' `name`/`owner`/`status`/`version`/`modifiedAt`), CloudNativePG (`cnpg.io/*`),
cert-manager, Calico, MetalLB, Authentik's outposts, Kubernetes itself. Ours, or stale:

- **Hand-written apps label their pods `app: <name>`**, and the Helm charts use
  `app.kubernetes.io/name`/`instance`. The `app` labels are Deployment and Service
  selectors, so they stay (selectors are immutable); the standard labels are added beside
  them through `commonMetadata`.
- **`env: production` and `db: postgres`** on the pod templates of `babybuddy-server` and
  `immich-power-tools` (`deployment-*.yaml`): free-form, and `env` overlaps
  `homelab.local/env`. Remove them (pod labels only; a rollout, no selector change).
- **Old Helm-convention labels** (`release`, `chart`, `heritage`) on nfs-client's and
  obsidian's charts: the charts' own, left alone.
- **`name` labels on the cert-manager, cnpg-system and metallb-system Namespaces**: the
  `kubernetes.io/metadata.name` label already says it. Check where each comes from
  (`namespace.yaml`, or an operator's install) before removing.
- **`nginx.ingress.kubernetes.io/proxy-body-size`** on immich's Ingress
  (`immich/app/values.yaml`): ingress-nginx is gone. Remove it.
- **`kubectl.kubernetes.io/last-applied-configuration`** on the Secrets
  `cert-manager/cloudflare-api-token` and `cloudflare-tunnel/cloudflare-tunnel-secret`: an
  old `kubectl apply`, carrying a copy of each Secret. Remove it with
  `kubectl annotate ... kubectl.kubernetes.io/last-applied-configuration-`.
- **babybuddy's NetworkPolicy already admits pods labelled `app: babybuddy-mcp`**
  (`networkpolicy-babybuddy-server.yaml`, a podSelector with no namespaceSelector), which
  can't match while the MCP is in its own namespace. After step 4 it does: check the MCP
  then reaches babybuddy through the policy and nothing more is opened.

## Checklist

- [x] Label set confirmed
- [x] Audit done and recorded
- [x] Generator knows `prod/<category>/`; tests pass on the moved tree
- [ ] Namespaces labelled; apps moved; Flux reconciled with no deletions
- [ ] babybuddy-mcp in `babybuddy`; old namespace deleted
- [ ] Annotations cleaned up
- [x] `CLAUDE.md` files and `scripts/README.md` say `prod/`

## Progress

- **Step 2 and the move are one commit**, since the pre-commit gate tests the real tree: the
  generator learned `prod/<category>/`, every namespace folder was `git mv`ed into its category,
  each `namespace.yaml` got its category label, the hand-written `ks.yaml` and HelmRelease
  files got their paths and labels, and the test overlay follows (`kubernetes/test/`, labelled
  `env: test`). Flux's dry runs: the 41 app Kustomizations and 26 Namespaces only drift (new
  path, new labels), and at their new paths nothing is created or deleted; 27 HelmReleases gain
  `commonMetadata`, one Helm upgrade each, with no pod-template change.
- **Pushed** (`68237f7`) and applied: 47 Kustomizations and 27 HelmReleases Ready, no pod
  restarted. Three Deployments that operators create rather than Flux have no labels: Authentik's
  two outposts and the tunnel controller's `controlled-cloudflared-connector`; each operator
  has its own way to label them (Authentik's outpost settings, the controller's chart values).
- **`data` and `exposure`** are Namespace labels capture works out from the cluster
  (`derived.py`), written into every `namespace.yaml`.
- **`login` is on hold:** proxy login shows on a route (the `authentik` middleware), but which
  apps use OIDC is known only inside Authentik, which capture can't read. Needs the owner's
  per-app values, or Authentik's API.
- **Not yet:** babybuddy-mcp's move, and the annotation cleanup.

## Resuming

Read this file, then `git log --oneline -- QUEUE-apps-layout.md kubernetes/prod`. Each step
lands in its own commit, and the tree is valid between steps: the generator learns the new
layout before anything moves.

## Done when

Every app is under `kubernetes/prod/<category>/`, every object in it carries the confirmed
labels, `kubectl get ns -l homelab.local/category=services` lists exactly the services, and a
fresh `capture` plus `generate` changes nothing.

## Checked and negative

- **Shared category namespaces** (bjw-s-labs/home-ops and onedr0p/home-ops: `network`,
  `database`, `selfhosted`, each holding several apps) don't fit here: most apps have their
  own namespace, and moving them would mean migrating volumes and databases.
- **kustomize `commonLabels`** also rewrites selectors, which are immutable on Deployments;
  Flux's `commonMetadata` adds labels without touching selectors.
