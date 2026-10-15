# app-template 3.7.3 -> 5.3.0

**The ask (2026-10-15):** continue `QUEUE-updates.md`; its next item is the bjw-s app-template
chart, two major versions behind on every hand-written release that uses it. Renovate's pull
request #8 bumps the version alone, which Flux can't apply (below); it closes itself once
`main` has 5.3.0 everywhere.

## What changes (checked before the first release moved)

Each release was rendered with `helm template` at 3.7.3 and at 5.3.0 from its own values
(`${VARIABLES}` replaced by placeholders, as Flux substitutes them first), and the two compared
object by object. For every release:

- **The selector label `app.kubernetes.io/component` becomes `app.kubernetes.io/controller`**
  (common 4.0) on every Deployment and StatefulSet, their pods and their Services. A
  Deployment's selector is immutable, so the upgrade fails: "spec.selector: ... field is
  immutable". **The app is down from that failure until its Deployment is recreated**: the
  failed upgrade has already applied the Service with the new selector, which no longer
  matches the old pod (Lidarr's ingress answered 503). Recreate it right after the failure.
- **A ServiceAccount per release, named after it, and no API token in the pods** (common 5.0).
  Only Homepage reads the Kubernetes API; it names its account and mounts the token.
- **ServiceMonitors' `jobLabel` becomes `app.kubernetes.io/name`** (Grimmory's and n8n's): the
  Services carry no label by the old name, so Prometheus named the job after the Service. n8n's
  Service is `n8n`, so its job stays `n8n`; Grimmory's MariaDB job goes from
  `grimmory-mariadb` to `grimmory`. Nothing in the repo selects either job by name.
- **Nothing else**: no object is renamed or removed (common 4.0's naming change gives the
  same names here), and no release uses `rawResources`, NetworkPolicies, static tokens or
  PodMonitors, the other things 4.0 and 5.0 changed.
- **Homepage alone needs new values**: `serviceAccount.create: true` is the 3.x syntax, refused
  by 5.x's schema.

## Rules

- **Per release: the version in its `helmrelease.yaml`, pushed; then delete the old workload and
  reconcile.** Once Flux reports `UpgradeFailed` on the selector, `kubectl delete deployment
  <name> -n <ns> --cascade=foreground` (and `statefulset` for Grimmory's MariaDB), then `flux reconcile hr <name> -n
  <ns> --reset`; the upgrade then creates them anew. Foreground, so the old pods are gone
  before the new one starts: a plain delete returns at once and the two overlap on the same
  claim (mesh-mini's old pod was still terminating beside its new one). Whether a release is done is read from
  its Deployment's selector (`app.kubernetes.io/controller`), never from the HelmRelease's
  history, which records the failed 5.3.0 attempt too. Tested
  2026-10-15 on a throwaway release: `spec.upgrade.force: true` doesn't help (Flux's Helm
  applies server-side and is refused the same way), deleting the Deployment does.
- **Applied means**: the HelmRelease Ready and Released on 5.3.0, the pod Ready on the same
  image, its Service's endpoints the new pod, and the app answering (its health path or page).
- **Deleting a StatefulSet keeps its claims**; the new one, same name, mounts them again.
  Grimmory's MariaDB data is on `local-db`: check the claim is Bound to the same volume after.
- **One release first, then batches of the same shape**, each batch verified before the next.

## Checklist

- [x] Bookshelf (the pilot, 2026-10-15): failed on the selector as expected, recreated in
      about 15 seconds; `/ping` 200 directly and through the ingress.
- [x] The other media apps: Lidarr, Prowlarr, qBittorrent, Radarr, SABnzbd, Seerr, Sonarr,
      Tdarr, Whisparr (2026-10-15): each about 20 seconds down, Lidarr about a minute.
- [x] Linkwarden, mesh-mini, Organizr, n8n (2026-10-15); n8n's metrics kept job `n8n`.
- [x] Homepage (2026-10-15): `serviceAccount: {homepage: {}}`, `controllers.main.serviceAccount.identifier:
      homepage`, `controllers.main.pod.automountServiceAccountToken: true`, the binding's
      subject `homepage`. Rendered 2026-10-15: same account name, binding and role as now.
      Recreated: its own account with the token, `can-i list pods` yes, no 403s in its log.
- [x] Grimmory (2026-10-15): a dump first, then both recreated; MariaDB back on the same
      worker and volume, metrics under job `grimmory`. Grimmory restarted once, having started
      before MariaDB took connections.
- [x] Jackett in the test overlay (`kubernetes/test/media/jackett/`, suspended): the version
      only, nothing running to recreate
- [x] Renovate's #8 closed itself (2026-10-15), finding 5.3.0 on `main` everywhere

## Resuming

`grep -rn 'version: 3.7.3' kubernetes/ --include=helmrelease.yaml` lists the releases left; the
first unticked box is next.

## Done when

Every app-template release is on 5.3.0 and verified, Renovate's #8 has closed, `QUEUE-updates.md`'s
item is ticked, and this file moves to `old-queues/`.
