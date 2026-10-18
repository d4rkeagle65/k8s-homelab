# Requests and limits on every container

**The ask (2026-10-11):** limits on everything. Booklore's first library scan, with no CPU limit,
took 1.2 cores on a 2-core worker and starved the databases beside it (health checks failed;
Authentik's and Immich's databases failed over). The owner chose generous CPU limits on
everything, a LimitRange per namespace, a PriorityClass for critical pods, and alerts.

## What the data said when this started

242 containers: 169 without a memory limit, 228 without a CPU limit, 156 without a memory request.
The 8-core worker had 7.8 of its 7.9 GiB memory promised in requests (27.5 GiB in limits), the
other two workers 3.2 and 2.1: inflated requests in some places and none in others pack pods onto
it. Right-sizing requests matters as much as adding limits.

## Rules (every batch sizes the same way)

From Prometheus: the peak and the 95th percentile over the history it has (only one day when this
started; re-measure at seven days, 2026-10-18, and tighten). CPU uses the 5-minute rate, which
smooths short bursts, hence the generous CPU limits.

- **Memory request:** 1.2 x peak, at least 64Mi, rounded up to 32Mi.
- **Memory limit: what the container can allocate under load, not what was sampled.** A scrape
  every 30 to 60 seconds misses short spikes: Immich's database never sampled above 150Mi and
  was OOM-killed at 384Mi by an upgrade's migrations. Never below the request.
  - **PostgreSQL:** `shared_buffers` + 4 x `maintenance_work_mem` (three autovacuum workers and
    one index build) + 20Mi per connection (`work_mem` for a sort and a hash, plus the backend
    itself) for twice the peak connections, at least 20, + 64Mi, rounded up to 128Mi. With
    CloudNativePG's settings here (160Mi, 64Mi, 4Mi) that's 1Gi; Authentik's 19 connections
    make 1280Mi. Read the settings from `pg_settings` and the peak from `cnpg_backends_total`.
    An extension that builds large indexes (Immich's vectorchord) gets more, with a comment.
  - **Other databases** (MariaDB, CouchDB): 768Mi.
  - **Apps:** 2.5 x peak, rounded up to 128Mi, at least 512Mi; the .NET media apps (Sonarr,
    Radarr, Lidarr, Prowlarr, Whisparr, Bookshelf) at least 1Gi for library scans and imports.
  - **Sidecars and exporters:** 2 x peak, at least 256Mi.
- **CPU request:** the 95th percentile, at least 10m, rounded up to 10m.
- **CPU limit:** 3 x peak, rounded up to 100m, between 500m and 1000m (two of the three workers
  have 2 cores; above 1000m a scan starves the databases beside it). Raise to 1000m when
  throttled in more than 10% of CFS periods over the window
  (`container_cpu_cfs_throttled_periods_total` / `container_cpu_cfs_periods_total`).
  Transcoders (Emby, Tdarr's node) 2000m. Databases 1000m.
- **Requests set where pods schedule; limits don't.** Raising a limit needs no node-fit check.
- A value set by hand for a known reason (a comment says why) stays, unless the data shows it's
  too low.
- **Before adding requests to pods pinned to a node** (a database instance on `local-db`, or
  anything on `nas-block`), check each node's allocatable memory against its requests plus the
  increase: a restarted instance that no longer fits stays Pending. Make room by moving a
  stateless pod: cordon the node, restart the pod, uncordon (restarting alone puts it back on
  the 8-core worker, below).
- Only what this repo owns. The control plane's static pods (API server, etcd, scheduler, controller
  manager) are left alone; operator-managed pods (Calico, MetalLB) through their operator's
  settings where it has some.

Queries (Prometheus, `kube-prometheus-stack`):

- peak memory: `max_over_time(container_memory_working_set_bytes{container!=""}[7d])`
- peak CPU: `max_over_time(rate(container_cpu_usage_seconds_total{container!=""}[5m])[7d:5m])`
- 95th percentile CPU: the same with `quantile_over_time(0.95, ...)`
- current settings: `kube_pod_container_resource_requests` / `_limits`

## Checklist

- [x] Batch 1: the media apps (`prod/apps/media/`): Bookshelf, Jellyseerr, Lidarr, Prowlarr,
      qBittorrent, Radarr, SABnzbd, Sonarr, Tdarr, Whisparr. Memory requests went from 4224Mi to
      3904Mi: Tdarr freed 1.28 GiB, while SABnzbd, Jellyseerr and Radarr were requesting less
      than they use
- [x] Batch 2: the CloudNativePG databases (each Cluster's `resources`; a change restarts its
      instances one at a time, replica first, then a switchover). Peaks 91-332Mi; 4.7 GiB of
      requests added across 24 instances, where none had any. Sonarr and Radarr were moved off
      the 8-core worker first, or one of its instances wouldn't have fit
- [x] Batch 3: the other apps: Authentik, Immich, n8n, Mealie, Baby Buddy, ManicTime, Home
      Assistant, Emby, Obsidian, Organizr, Homepage, Grimmory with its MariaDB and exporter.
      Judgement over the rules, each said in a comment: Emby keeps 2 cores and 4 GiB for
      transcoding; Immich's server gets a core (thumbnails, transcodes) and its machine learning
      a 2 GiB limit (models load far above the idle peak); n8n's task runners keep 1 GiB;
      Grimmory's CPU request stays 100m (its 95th percentile was library scans). Authentik's
      two outposts are created by Authentik, not this repo: batch 6. Re-measured 2026-10-18
      from the 4.2 days since 2026-10-14 16:48, raising only: Immich's machine learning 2304Mi
      and 620m (the import's, a comment says), Linkwarden 960Mi and 100m, Authentik's server
      992Mi in 2Gi, Zammad's websocket 448Mi in 1Gi and 1000m (throttled 17%) and scheduler
      672Mi, Elasticsearch 1184Mi in 1536Mi (952Mi resident in 1Gi; the heap stays 512m),
      Lidarr and Prowlarr 352Mi, Tdarr 320Mi and 256Mi; the sync Jobs got the CPU limits they
      lacked. Immich's server waits for the import (the last item)
- [x] Batches 1-3's limits recalculated by the load rules above, after Immich's database was
      OOM-killed. Requests unchanged. Alloy's limit went to 512Mi ahead of batch 5 (it was at
      87%)
- [x] Batch 4: the platform (2026-10-12), by the rules from seven days of data, all of it
      light (the busiest, the Bitwarden CLI, peaked at 158Mi and 34m). Judgement, each said in a
      comment: a CPU request above the 95th percentile where others wait on it (the
      CloudNativePG operator 100m and Barman plugin 50m, both Traefiks, the cert-manager and
      External Secrets webhooks, the Cloudflare connector, Mosquitto: 50m to 100m); the main
      Traefik and the Cloudflare connector keep a 1000m CPU limit for large uploads; Redis
      sized as one of the other databases. metrics-server's chart default (100m, 200Mi) went
      to the rules' 10m and 64Mi. The main Traefik also got a second replica on another node.
      Redis-ha's config-init init container has none; it runs once at start
- [x] Batch 5 (2026-10-16): monitoring. Every container had a memory request and limit except
      the charts' sidecars; the batch adds the CPU limits (500m; Prometheus 1000m, as a
      database: compactions) and the sidecars' settings (the config reloaders, Grafana's and
      Loki's sidecars, the SNMP exporter's reloader: 64-128Mi requests, 256Mi limits).
      Re-measured 2026-10-18 from the 4.2 days since 2026-10-14 16:48, raising only: Prometheus
      2432Mi in 5Gi (its 2020Mi peak was 99% of 2Gi), Alloy 384Mi in 768Mi on every node,
      kube-state-metrics 256Mi in 640Mi, Grafana 640Mi in 1408Mi, the exporters the rules' 64Mi
      in 256Mi
- [x] Batch 6 (2026-10-16): operator-managed. Calico through its operator's resources
      (`Installation`'s calico-node, typha, kube-controllers and CSI driver; `APIServer`,
      `Goldmane`, `Whisker`; calico-node's CPU limit 1000m, as it carries every pod's network;
      each node had the room for the per-node pods' requests, the control planes 2.2Gi), the
      tigera-operator itself (sized from its use: its peak was startup), MetalLB's controller and
      speakers. Flux's controllers already had theirs
- [x] A LimitRange in each app namespace with defaults for a container that sets none
      (2026-10-16, `prod/system/cluster-policy/`, one per apps and services namespace)
- [x] A PriorityClass for the databases, Authentik, Traefik and monitoring (2026-10-16,
      `homelab-critical`, in the same release)
- [x] Alerts (2026-10-18): `ContainerWithoutLimits` and `ContainerMemoryNearLimit` (`monitors/`,
      each a ticket). Heavy throttling is the chart's `CPUThrottlingHigh`, severity `info`, which
      zammad-bridge leaves out (13 firing on 2026-10-18, so a ticket each would be noise until
      the 10-21 re-measure raises the throttled limits). Zammad's nginx, websocket, Redis and
      memcached and `proxmox-endpoints` got the CPU limits they lacked first
- [x] Authentik's two outposts (LDAP and the proxy) restarted (2026-10-18), so they took
      `authentik`'s LimitRange defaults (500m, 512Mi); Authentik owns their Deployments, not
      Flux. Every running container outside Talos's own now has limits, and
      `ContainerWithoutLimits` matched none once it was live
- [x] Risk fixes from the four days there were (2026-10-18), limits only, so nothing
      rescheduled: CPU 1000m where throttled in over 10% of periods (democratic-csi's drivers
      and registrar 33-77%, nebula-sync 45%, the Bitwarden CLI 24%, redis-ha's split-brain check
      25%, Organizr 16%, the pve-exporter 14%, Goldmane 13%, node-exporter 11%);
      kube-state-metrics' memory 512Mi (212Mi peak in 256Mi); Immich's machine learning 3Gi
      (1865Mi peak in 2Gi during the import), a judgement said in its comment
- [x] The Docker VM's containers (2026-10-16): a `mem_limit` on every long-running service in
      `docker/` (the VM has no swap, so it's a hard ceiling), about two to three times its
      cAdvisor peak over the day and a half of history there was; `tests/test-docker-stacks.py`
      fails a running service without one. Garage's (on the NAS, 2g) is unmeasured: nothing
      scrapes its memory yet.
- [ ] Re-measure the Docker VM's containers with the cluster's, from a week of cAdvisor data, and
      give Garage a figure once its memory is scraped
- [ ] Re-measure at seven days, **from 2026-10-21**: Prometheus's history starts 2026-10-14 16:48
      (its data was reset then), so a week of it exists from the 21st. Requests from the new
      peaks, limits by the rules above. What four days showed, for that pass:
      - Memory requests far below peak: Immich's machine learning (384Mi, 1865Mi peak), Immich's
        server (1376Mi, 5401Mi, the import), Prometheus (768Mi, 1358Mi), Alloy (64Mi per node,
        297Mi), Flux's controllers (64Mi, up to 262Mi: `flux install`'s defaults in
        `gotk-components.yaml`, so a change is a patch in `flux/config/flux-system/`),
        kube-state-metrics (64Mi, 212Mi), Linkwarden (512Mi, 793Mi); most others a little below.
      - At the 1000m cap and still throttled: Immich's server 22% and machine learning 15%
        (both during the import), Flux's kustomize-controller 13%, Elasticsearch 10%. Each needs
        a judgement against the cap, as the rules say.
      - Elasticsearch at 87% of its 1Gi: its JVM, sized by the chart (`QUEUE-zammad.md`).
      - Bookshelf (throttled 14%, 224Mi requested for a 700Mi peak) and rreading-glasses (84% of
        its 256Mi) belong to the session working on them (2026-10-18): leave them to it, or
        include them only once it's done.
      - Immich's server memory limit is 6Gi while the owner's library import runs (3Gi was
        OOM-killed within 30 seconds of each start by the import's jobs); size it again once the
        import is done.

## Resuming

Rerun the queries above; the first unticked batch is next. A batch is one commit.

## Done when

Every container has requests and limits, the workers' memory requests are balanced, every box is
ticked, and this file moves to `old-queues/`.
