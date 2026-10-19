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

From Prometheus: the peak and the 95th percentile over the history it has (a week from
2026-10-21; the 2026-10-18 pass had 4.2 days, so it only raised). CPU uses the 5-minute rate,
which smooths short bursts, hence the generous CPU limits.

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
- **CPU limit:** 3 x peak, rounded up to 100m, between 500m and 1000m (above 1000m a scan
  starves the databases beside it; whether that still holds is the owner's question below).
  Raise to 1000m when throttled in more than 10% of CFS periods over the window
  (`container_cpu_cfs_throttled_periods_total` / `container_cpu_cfs_periods_total`).
  Transcoders (Emby, Tdarr's node) 2000m. Databases 1000m.
- **Requests set where pods schedule; limits don't.** Raising a limit needs no node-fit check.
- A value set by hand for a known reason (a comment says why) stays, unless the data shows it's
  too low.
- **Before adding requests to pods pinned to a node** (a database instance on `local-db`, or a
  DaemonSet's pod on every node), check each node's allocatable memory against its requests plus the
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
- **Query each Prometheus replica, not the Service.** The two replicas each keep their own
  history, and `kps-prometheus` sends a query to either, so the same query answers differently
  from one call to the next. Ask each pod
  (`/api/v1/namespaces/monitoring/pods/prometheus-kps-0:9090/proxy/api/v1/query`, and `-1`)
  and take the higher peak, over the window `prometheus_tsdb_lowest_timestamp_seconds` gives.
- per workload rather than per pod: join on `kube_pod_owner` (a ReplicaSet's name less its hash
  is the Deployment), so a pod replaced in the window still counts
- PostgreSQL's limit: `cnpg_pg_settings_setting` and the peak of `cnpg_backends_total` summed
  per pod; the Barman sidecar is `container="plugin-barman-cloud"`, an init container to
  `kube_pod_init_container_resource_*`

## Checklist

- [x] Batch 1: the media apps (`prod/apps/media/`): Bookshelf, Jellyseerr, Lidarr, Prowlarr,
      qBittorrent, Radarr, SABnzbd, Sonarr, Tdarr, Whisparr. Memory requests went from 4224Mi to
      3904Mi: Tdarr freed 1.28 GiB, while SABnzbd, Jellyseerr and Radarr were requesting less
      than they use
- [x] Batch 2: the CloudNativePG databases (each Cluster's `resources`; a change restarts its
      instances one at a time, the primary in place: `kubernetes/CLAUDE.md`). Peaks 91-332Mi;
      4.7 GiB of requests added across 24 instances, where none had any. Sonarr and Radarr were
      moved off the 8-core worker first, or one of its instances wouldn't have fit. Re-measured
      2026-10-18 from the 4.2 days since 2026-10-14 16:48, raising only: Immich's 384Mi, ManicTime's,
      Whisparr's and Zammad's 288Mi, Prowlarr's 160Mi; limits by the PostgreSQL rule from the
      peak connections (Authentik 21: 1408Mi, Zammad 19: 1280Mi, Kea 896Mi); each node had the
      room. The Barman sidecars' resources in five namespaces' `ObjectStore`s (the rest: the
      last item)
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
      Redis-ha's config-init init container has none; it runs once at start. Re-measured
      2026-10-18 from the 4.2 days since 2026-10-14 16:48, raising only: about 30 memory requests to
      1.2 x peak (External Secrets' three to 160-192Mi, external-dns 160Mi, democratic-csi's
      node driver 192Mi, the Bitwarden CLI 256Mi and 30m, both Traefiks), Renovate's run
      1536Mi and 540m in 3200Mi, `proxmox-endpoints` 1000m (throttled 11%), the CloudNativePG
      operator 1000m (318m peak)
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
      speakers. Flux's controllers already had theirs. Re-measured 2026-10-18 from the 4.2 days
      since 2026-10-14 16:48, raising only: Flux's memory requests over `flux install`'s 64Mi by a
      patch in `flux/config/flux-system/` (source 320Mi, kustomize 256Mi and 250m, helm 192Mi,
      notification 128Mi), the tigera-operator 416Mi (180-230Mi most of the time), typha 192Mi,
      Goldmane 224Mi. kustomize-controller and Goldmane stay at the 1000m cap, throttled 14% and
      13%: their work only takes longer
- [x] A LimitRange in each app namespace with defaults for a container that sets none
      (2026-10-16, `prod/system/cluster-policy/`, one per apps and services namespace)
- [x] A PriorityClass for the databases, Authentik, Traefik and monitoring (2026-10-16,
      `homelab-critical`, in the same release)
- [x] Alerts (2026-10-18): `ContainerWithoutLimits` and `ContainerMemoryNearLimit` (`monitors/`,
      each a ticket). Heavy throttling is the chart's `CPUThrottlingHigh`, severity `info`, which
      zammad-bridge leaves out (13 firing on 2026-10-18, so a ticket each would be noise: most
      are at the 1000m cap, the owner's question below). Zammad's nginx, websocket, Redis and
      memcached and `proxmox-endpoints` got the CPU limits they lacked first
- [x] Authentik's two outposts (LDAP and the proxy) restarted (2026-10-18), so they took
      `authentik`'s LimitRange defaults (500m, 512Mi); Authentik owns their Deployments, not
      Flux. Every running container outside Talos's own now has limits, and
      `ContainerWithoutLimits` matched none once it was live; the Barman sidecars, which it
      doesn't see, are in the recheck item
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
- [ ] The Docker VM's containers, re-measured 2026-10-18 from the 4.2 days of cAdvisor data:
      NetBox 2g -> 4g (1525Mi, its steady use), Semaphore 2g -> 3g (1311Mi, a task run),
      cAdvisor 768m -> 1g (421Mi); the rest were at two to seven times their peak. Left: again
      from a week, with the cluster's; the second Docker VM (`kea-standby`), whose cAdvisor isn't
      scraped; Garage, whose metrics have no memory figure
- [ ] Recheck at a full week, **from 2026-10-21 16:48** (Prometheus's history starts 2026-10-14
      16:48). The 2026-10-18 pass (each batch's "Re-measured" above) had 4.2 days and only
      raised, so this one:
      - raises any request a 7-day peak now exceeds, mainly weekly jobs the short window missed;
      - lowers what's above the rules: SABnzbd (736Mi and 270m for 111Mi and 1m), Seerr (576Mi,
        222Mi), Sonarr (608Mi and 110m, 282Mi and 18m), authentik-db (416Mi, 247Mi), Elastic's
        operator (160Mi, 55Mi), and the CPU requests above the 95th percentile that no comment
        keeps (Zammad's Rails server 100m and scheduler 50m, Home Assistant 60m, Loki 50m);
      - sets the Barman sidecar's resources in the other namespaces' `ObjectStore`s (babybuddy,
        linkwarden, mealie, n8n, media; media's also serves the Bookshelf and rreading-glasses
        databases, so with the session working on those; each restarts that namespace's
        instances, the primaries in place, so with the owner: Authentik's takes every login
        with it). Their pods predate the LimitRanges,
        so they have none until recreated, and `ContainerWithoutLimits` doesn't see them: it
        reads `kube_pod_container_resource_limits`, not the init containers' series;
      - sizes Immich's machine learning again once the import is done: its 2304Mi and 620m are
        the import's.
      Init containers that run once at start have none: babybuddy's check-db-ready and mealie's
      wait-for-db take their namespace's defaults; the Bitwarden CLI's fetch-cli gets nothing
      (external-secrets has no LimitRange).
      Nobody holds this: the session that made the 4.2-day pass was closed on 2026-10-18. Its
      per-container figures are in the batches' pull requests (#57, #58, #60, #62, #63, #64).
      Where things stood that evening, for comparison: memory requests 77% of the nodes'
      allocatable and use 52%; one worker (the 9.7GiB one) at 93% requested, so a raise there
      needs the node-fit check above; CPU requests 34%, use 29%
- [ ] Immich's server, with the owner (asked in its pull request, the apps batch): its 4.2 days
      are the import (5401Mi peak in the 6Gi limit set for it, p95 992m at the 1000m cap,
      throttled in 26% of periods) against requests of 1376Mi and 30m. Sized once the import is
      done, or now with a limit above 6Gi (3Gi was OOM-killed within 30 seconds of each start)
- [x] `primaryUpdateMethod: switchover` on every Cluster with a replica (the owner, 2026-10-18),
      after the re-measure's in-place restarts took Authentik down for about 3.5 minutes: all 15
      (`kubernetes/CLAUDE.md`; `test_replicated_databases_switch_over` holds new ones to it).
      rreading-glasses-db has one instance and restarts in place either way
- [ ] The owner: whether the 1000m CPU cap rises. It was set when two of three workers had 2
      cores; the four workers now have 3, 4, 4 and 6 (the control planes 2). At the cap and
      throttled on 2026-10-18: democratic-csi's drivers 66-79% of periods, Renovate 47%,
      nebula-sync 46%, Immich's server 26% and machine learning 15% (the import), the Bitwarden
      CLI 25%, Organizr 16%, kustomize-controller and pve-exporter 14%, Goldmane and redis-ha's
      split-brain check 13%. Those figures span the time at 500m; in the hour after the raise
      to 1000m most fell to 0-6%. democratic-csi's driver (27%) and Grafana's dashboard sidecar
      (32%) stayed throttled while averaging 3-7m: short bursts across cores within one 100ms
      period, which a higher cap would barely change. Judge the cap on a week at 1000m
- [ ] Bookshelf, its database and rreading-glasses, once the session working on them is done
      (2026-10-18 data): Bookshelf 700Mi peak for a 224Mi request, p95 499m at its 500m limit
      (throttled 14%; the rules give 864Mi, 500m, 1000m), rreading-glasses 215Mi in 256Mi (64Mi
      requested), rreading-glasses-db 305Mi for 128Mi and p95 52m for 10m, bookshelf-db p95 53m
      for 20m
- [ ] Priority tiers (the owner, 2026-10-18), so a node short of memory sheds the media apps
      first. The classes `homelab-important` (100000) and `homelab-low` (-1000, never preempts)
      are in `cluster-policy` beside `homelab-critical`. Left, in this order:
      - once both classes are live (a pod naming a class that doesn't exist is rejected),
        `homelab-important` on Home Assistant, Immich (server, machine learning, power tools),
        Obsidian, Mealie, Baby Buddy, Linkwarden, n8n, Grimmory, Homepage and Headlamp, and
        `homelab-low` on the media apps;
        their databases stay `homelab-critical`. Each pod restarts once to take its class;
      - the workers' kubelet evicting before the kernel's OOM killer acts: a soft threshold
        (memory.available under 1Gi for a minute) and a hard one above the default 100Mi, a
        worker-only patch in `talos/patches/`, applied node by node by the owner (`tofu/CLAUDE.md`)

## Resuming

Rerun the queries above; the first unticked batch is next. A batch is one branch and pull
request.

## Done when

Every container has requests and limits, the workers' memory requests are balanced, every box is
ticked, and this file moves to `old-queues/`.
