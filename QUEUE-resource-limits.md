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
- Only what this repo owns. kubeadm's static pods (API server, etcd, scheduler, controller
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
      two outposts are created by Authentik, not this repo: batch 6
- [x] Batches 1-3's limits recalculated by the load rules above, after Immich's database was
      OOM-killed. Requests unchanged. Alloy's limit went to 512Mi ahead of batch 5 (it was at
      87%)
- [ ] Batch 4: the platform: Traefik (both), cert-manager, External Secrets, ExternalDNS, the
      CloudNativePG operator and Barman plugin, local-path, the NFS provisioners, metrics-server,
      the Cloudflare tunnel, the SMTP relay, Redis, Mosquitto, No-IP, nebula-sync
- [ ] Batch 5: monitoring (Prometheus, Alertmanager, Grafana, Loki, Alloy, the exporters)
- [ ] Batch 6: operator-managed: Calico (its Installation), MetalLB, Flux
- [ ] A LimitRange in each app namespace with defaults for a container that sets none
- [ ] A PriorityClass for the databases, Authentik, Traefik and monitoring
- [ ] Alerts: a container near its memory limit, heavy CPU throttling, a container without limits
      (with monitoring phase 5)
- [ ] The scheduler weighs memory and CPU equally, so the 8-core worker always scores as the
      emptiest and attracts pods until its memory is full (2026-10-11: 97%, evictions). Weight
      memory higher: kube-scheduler's `NodeResourcesFit` with `LeastAllocated` and
      `resources: [{name: memory, weight: 3}, {name: cpu, weight: 1}]`, through kubeadm's
      scheduler config (Ansible, `k8s-upgrade.yml`'s territory)
- [ ] Re-measure at seven days (2026-10-18): requests from the new peaks, limits by the rules
      above. Immich's server was throttled in 66% of periods over the first two days, mostly
      while it crash-looped on 2026-10-11; if it stays above 10% it needs a judgement against
      the 1000m cap

## Resuming

Rerun the queries above; the first unticked batch is next. A batch is one commit.

## Done when

Every container has requests and limits, the workers' memory requests are balanced, every box is
ticked, and this file moves to `old-queues/`.
