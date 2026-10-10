# Monitoring: Prometheus, Loki and Grafana

**The ask:** metrics and log aggregation for the cluster and the machines around it, with
Grafana in front, light enough for these nodes (8 GiB workers, 4 GiB control planes). The
owner picked Prometheus, Loki and Grafana over VictoriaMetrics; help with tuning log search
comes with it. Proxmox forwards metrics and logs too, and so does every application that can.

## Settled

- **Charts, used as published:** `kube-prometheus-stack` (prometheus-community: the
  Prometheus Operator, Prometheus, Alertmanager, Grafana, node-exporter, kube-state-metrics),
  `loki` and `alloy` (Grafana). Promtail is end-of-life; Alloy collects the logs.
- **Layout:** namespace `monitoring` (category `system`), one `.handwritten` release folder per
  chart holding **only** its HelmRelease (values inline), and `monitoring-extras`
  (`.handwritten`, stays that way) for everything else: Secrets, dashboards, datasources the
  charts don't make. That keeps the later conversion to captured releases clean.
- **Conversion to captured** (last phase), per release: remove `.handwritten`, `capture`,
  `generate`, then check that the captured `values.yaml` matches what was in git and that
  Flux has nothing to change. Write the recipe into `scripts/README.md` the first time.
- **Storage:** Prometheus and Alertmanager on `nas-block`, a directory on each worker's own
  iSCSI LUN on the NAS (`ansible/tasks/proxmox-nas-block.yml`), so their constant writes land on
  the NAS's hard drives, not the hosts' SSDs; a block device, since Prometheus's docs rule out NFS.
  Grafana on `nfs-retain-rwo` (its own small SQLite: users and UI-made dashboards survive a pod
  move). Loki's chunks go to Garage on the NAS (S3), only its write-ahead log stays local.
- **No single host:** two Prometheus replicas, each scraping everything into its own volume,
  and two clustered Alertmanagers, each required on a different worker. A host's loss loses
  neither metrics nor alerting; Loki in Garage can restart on any worker.
- **Light writes:** scrape and rule evaluation every 60 seconds; the API server's latency and
  size histograms dropped at scrape time (most of a small cluster's series), with the four
  rule groups that read them off.
- **Retention:** Prometheus 15 days, capped at 18 GiB per replica (each `nas-block` mount is
  28 GiB); Loki 14 days. Logs are for diagnosis, not records.
- **Placement:** by resource requests, set on everything, plus the replicas' hard
  anti-affinity. Worker 1 has the most memory requested, so the pairs usually land on 2 and 3.
- **Memory budget** (requests / limits, per pod): Prometheus 768 MiB / 2 GiB (two of them),
  Loki 384 MiB / 1 GiB, Grafana 384 MiB / 1 GiB, Alertmanager 64 / 128 MiB (two), the
  operator 64 / 256 MiB, kube-state-metrics 64 / 256 MiB, node-exporter and Alloy about
  32-128 MiB per node. Loki's memcached caches, gateway, canary and MinIO are off (their
  memory is most of Loki's default footprint, and single-binary mode doesn't need them).
- **Control-plane components:** kubeadm binds the scheduler, controller-manager and etcd
  metrics to 127.0.0.1, so their scrapes and alert rules are off at first rather than firing
  "target down" forever. Turning them on is a kubeadm setting (phase 5).
- **Access:** Grafana at `grafana${SUBDOMAIN_SUFFIX}.${BASE_DOMAIN}`, private-only (LAN,
  cluster and tailnet), logging in through Authentik (a blueprint in `authentik-extras`):
  members of `Grafana Admins` become Admins, of `Grafana Viewers` Viewers, nobody else gets in.
  The local admin (`grafana-admin`) stays for when Authentik is down. Prometheus and
  Alertmanager have no Ingress; `kubectl port-forward` when needed.
- **One client secret, one field:** Authentik's copy is a captured Secret in `authentik-extras`,
  whose variable capture names `AUTHENTIK_GRAFANA_OIDC_CLIENT_SECRET` (base64); Grafana's Secret
  in `monitoring-extras` uses the same variable.

## Owner inputs

| Needed before | What | State |
|---|---|---|
| Phase 1 merge | `GRAFANA_ADMIN_USER`, `GRAFANA_ADMIN_PASSWORD`, `AUTHENTIK_GRAFANA_OIDC_CLIENT_SECRET` in the `cluster-secrets` Vaultwarden item | present (2026-10-10) |
| Phase 1 live | the owner in the `Grafana Admins` group (made by the blueprint) | after the merge |
| Phase 2 | a Garage bucket `loki-logs` and its key (`LOKI_S3_ACCESS_KEY_ID`, `LOKI_S3_SECRET_ACCESS_KEY`) | present (2026-10-10) |
| Phase 3 | a read-only Proxmox token for the exporter (`PVEAuditor`); SNMP on, read-only, on both NASes | present (2026-10-10) |
| Phase 5 | where alerts go (email through the relay, n8n, or both) | a decision |

## Phases

1. **Metrics and Grafana:** HelmRepository `prometheus-community`; namespace
   `monitoring`; `kube-prometheus-stack` (two Prometheus and two Alertmanager replicas on
   `nas-block`, Alertmanager with a null receiver for now); `monitoring-extras` with Grafana's
   admin and Authentik Secrets; the Grafana Ingress; the Authentik blueprint and groups.
2. **Logs:** HelmRepository `grafana` (added with its first release: an unused source fails the
   generated-header check); `loki` (single binary, chunks in Garage) and `alloy` (a DaemonSet reading
   `/var/log/pods`, plus Kubernetes events from one instance); Loki as a Grafana datasource.
3. **More metrics**, everything that exposes them:
   - Proxmox: `prometheus-pve-exporter` in the cluster (cluster, guests, storage) with a
     read-only token; node-exporter and `smartctl_exporter` on each host through Ansible
     (ZFS, disks, and each SSD's wear: percentage used and data written).
   - Around the cluster: the Synology and the TerraMaster over SNMP (the SNMP exporter, a
     read-only community); the Pi-holes (a Pi-hole exporter); the Docker VM (node-exporter
     and cAdvisor); Kea through Stork's Prometheus endpoint; Garage's `/metrics`.
   - In the cluster: CloudNativePG PodMonitors (`monitoring.enablePodMonitor` on each
     Cluster), Traefik, Flux, cert-manager, ExternalDNS, MetalLB, Authentik, immich, n8n
     (`N8N_METRICS`), the *arr apps (`exportarr`), qBittorrent, Home Assistant.
4. **Logs from outside the cluster:** Alloy on the Proxmox hosts, the Pi-holes and the Docker
   VM through `ansible/baseline.yml` (the journal, and Docker's container logs on the Docker
   VM), pushing to Loki through a private route; the two NASes' syslog, received by Alloy.
5. **Alerting:** Alertmanager's receivers; the control-plane metrics bound to the node address
   (kubeadm, through Ansible) and their rules on; a dead-man's switch outside the cluster.
6. (Folded into phase 1: Grafana's Authentik login.)
7. **Convert the three releases to captured** (Settled, above).
8. **Log search tuning:** labels kept few (namespace, app, node, stream), the rest as
   structured metadata; saved queries and dashboards for the common questions.

## Checklist

- [x] Phase 1 built
- [x] Phase 1 live (2026-10-10): 40 of 40 targets up on both replicas, only Watchdog firing,
      Grafana and its Authentik login reachable, the pairs on different workers; after the
      drops about 103,000 series kept of 244,000 scraped, about 2,750 samples a second
- [x] Phase 2 built: Loki (single binary, chunks in `loki-logs`, an emptyDir for the rest) and
      Alloy (a DaemonSet on every node, pod logs from `/var/log/pods`); Loki is a Grafana data
      source. Kubernetes events are still to add (one collector, not one per node).
- [x] Phase 2 live (2026-10-10): all six nodes' Alloy sending (31 namespaces), Loki writing chunks
      to `loki-logs` (about 600 in the first hour, the start-up backlog included) and searchable;
      a test client with Loki's key reads chunks back. The first start's backlog (log files
      reaching back to 2025) was rejected as older than 7 days, as configured, and stopped.
- [ ] Loki's 10 failed reads from Garage at its first start (`loki_s3_request_duration_seconds_count`,
      `S3.GetObject`, status 500), with no log line naming them: if the count grows, read
      Garage's log on the NAS for the same minute. Reads by a test client succeed.
- [ ] Kubernetes events into Loki: one collector for the cluster (a one-replica Alloy
      Deployment with `loki.source.kubernetes_events`), not the per-node DaemonSet.
- [x] Phase 3A built: monitors for every database instance (one PodMonitor, `monitors/`), Flux,
      Traefik, cert-manager, ExternalDNS, MetalLB, Authentik, immich and n8n; the CloudNativePG
      dashboard; Grafana loads dashboards from any namespace.
- [ ] Phase 3A live: each new target up
- [x] Phase 3B built: the Proxmox API through `prometheus-pve-exporter` 3.10.1 (`pve-exporter/`,
      token `prometheus@pve!exporter`, PVEAuditor, from `PVE_EXPORTER_TOKEN_VALUE`; one host's API,
      `PVE_API_HOST`, with a verified certificate)
- [x] Phase 3C built: Debian's node-exporter on the Proxmox hosts, the Pi-holes and the Docker VM
      (`ansible/tasks/host-exporters.yml`, run by `baseline.yml`, off until `host_exporters`),
      with the packaged SMART and NVMe collectors on the Proxmox hosts; Prometheus finds the
      hosts through `proxmox-hosts.<domain>` (written by `proxmox-acme.yml`) and the Docker VM
      by its management name. Not yet: cAdvisor for the Docker VM's containers.
- [ ] Phase 3C live: `host_exporters: true` and `management_network` in the variable group, a
      baseline run, a Proxmox host-settings run (the shared name), every host up
- [ ] Phase 3D: the NASes (SNMP, `SNMP_COMMUNITY`), the Pi-holes, Garage, the *arr apps,
      qBittorrent, Home Assistant
- [ ] Phase 4
- [ ] Phase 5
- [x] Phase 6 (in phase 1)
- [ ] Phase 7
- [ ] Phase 8

## Resuming

`flux get kustomizations -n flux-system | grep -e monitoring -e loki -e alloy -e kube-prometheus`
shows what is deployed; the first unticked box is next. Each phase works without the later
ones.

## Done when

Every phase is ticked, the three releases are captured, and this file moves to `old-queues/`.
