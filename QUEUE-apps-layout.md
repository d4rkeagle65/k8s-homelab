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

## To confirm before the generator changes

The label set beyond `env` and `category` (suggested; see the table). Each needs a source
of truth that keeps it right without hand upkeep.

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

## Checklist

- [ ] Label set confirmed
- [ ] Audit done and recorded
- [ ] Generator knows `prod/<category>/`; tests pass on the moved tree
- [ ] Namespaces labelled; apps moved; Flux reconciled with no deletions
- [ ] babybuddy-mcp in `babybuddy`; old namespace deleted
- [ ] Annotations cleaned up
- [ ] `CLAUDE.md` files and `scripts/README.md` say `prod/`

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
