# Monitoring: Prometheus, Loki and Grafana

**The ask:** metrics and log aggregation for the cluster and the machines around it, with
Grafana in front, light enough for these nodes (8 GiB workers, 4 GiB control planes). The
owner picked Prometheus, Loki and Grafana over VictoriaMetrics; help with tuning log search
comes with it.

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
| Phase 2 | a Garage bucket `loki-logs` and its key | not yet |
| Phase 3 | a read-only Proxmox token for the exporter (`PVEAuditor`) | not yet |
| Phase 5 | where alerts go (email through the relay, n8n, or both) | a decision |

## Phases

1. **Metrics and Grafana:** HelmRepository `prometheus-community`; namespace
   `monitoring`; `kube-prometheus-stack` (two Prometheus and two Alertmanager replicas on
   `nas-block`, Alertmanager with a null receiver for now); `monitoring-extras` with Grafana's
   admin and Authentik Secrets; the Grafana Ingress; the Authentik blueprint and groups.
2. **Logs:** HelmRepository `grafana` (added with its first release: an unused source fails the
   generated-header check); `loki` (single binary, chunks in Garage) and `alloy` (a DaemonSet reading
   `/var/log/pods`, plus Kubernetes events from one instance); Loki as a Grafana datasource.
3. **More metrics:** CloudNativePG PodMonitors (`monitoring.enablePodMonitor` on each Cluster),
   Traefik, Flux, cert-manager, Garage, the Proxmox hosts (`prometheus-pve-exporter` with
   the read-only token), the Pi-holes.
4. **Logs from outside the cluster:** Alloy on the Proxmox hosts, the Pi-holes and the Docker
   VM through `ansible/baseline.yml` (journald and Docker), pushing to Loki through a
   private route.
5. **Alerting:** Alertmanager's receivers; the control-plane metrics bound to the node address
   (kubeadm, through Ansible) and their rules on; a dead-man's switch outside the cluster.
6. (Folded into phase 1: Grafana's Authentik login.)
7. **Convert the three releases to captured** (Settled, above).
8. **Log search tuning:** labels kept few (namespace, app, node, stream), the rest as
   structured metadata; saved queries and dashboards for the common questions.

## Checklist

- [x] Phase 1 built
- [x] Phase 1 live (2026-10-10): 40 of 40 targets up on both replicas, only Watchdog firing,
      Grafana and its Authentik login reachable, the pairs on different workers
- [ ] Phase 2
- [ ] Phase 3
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
