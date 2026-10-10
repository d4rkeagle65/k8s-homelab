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
- **Storage:** Prometheus and Loki on `local-db` (node-local; Prometheus's docs rule out NFS
  for its database), Grafana on `nfs-retain-rwo` (its own small SQLite: users and UI-made
  dashboards survive a pod move). Alertmanager on `local-db`, 1 GiB.
- **Retention:** Prometheus 15 days, capped at 18 GiB on a 20 GiB volume; Loki 14 days on
  20 GiB. Logs are for diagnosis, not records.
- **Placement:** by resource requests, set on everything. Worker 1 has 77% of its memory
  requested, so the scheduler keeps the heavy pods on the others without naming nodes in git.
- **Memory budget** (requests / limits): Prometheus 768 MiB / 2 GiB, Loki 384 MiB / 1 GiB,
  Grafana 192 MiB / 512 MiB, Alertmanager 64 / 128 MiB, the operator 64 / 256 MiB,
  kube-state-metrics 64 / 256 MiB, node-exporter and Alloy about 32-128 MiB per node. Loki's
  memcached caches, gateway, canary and MinIO are off (their memory is most of Loki's
  default footprint, and single-binary mode doesn't need them).
- **Control-plane components:** kubeadm binds the scheduler, controller-manager and etcd
  metrics to 127.0.0.1, so their scrapes and alert rules are off at first rather than firing
  "target down" forever. Turning them on is a kubeadm setting (phase 5).
- **Access:** Grafana at `grafana${SUBDOMAIN_SUFFIX}.${BASE_DOMAIN}`, private-only (LAN,
  cluster and tailnet), its own admin login until Authentik (phase 6). Prometheus and
  Alertmanager have no Ingress; `kubectl port-forward` when needed.

## Owner inputs

| Needed before | What | Where |
|---|---|---|
| Phase 1 merge | `GRAFANA_ADMIN_USER` (e.g. `admin`) and `GRAFANA_ADMIN_PASSWORD` (a new random one, letters and digits only) | two fields of the `cluster-secrets` Vaultwarden item; the mapping in `kubernetes/secrets/cluster-secrets.yaml` is in the phase 1 commit, so the fields must exist before it merges |
| Phase 3 | a read-only Proxmox token for the exporter (`PVEAuditor`) | Vaultwarden |
| Phase 5 | where alerts go (email through the relay, n8n, or both) | a decision |
| Phase 6 | the Authentik client secret | Vaultwarden, as for other blueprints |

## Phases

1. **Metrics and Grafana:** HelmRepository `prometheus-community`; namespace
   `monitoring`; `kube-prometheus-stack` (Alertmanager with a null receiver for now);
   `monitoring-extras` with the Grafana admin Secret; the Grafana Ingress.
2. **Logs:** HelmRepository `grafana` (added with its first release: an unused source fails the
   generated-header check); `loki` (single binary, filesystem) and `alloy` (a DaemonSet reading
   `/var/log/pods`, plus Kubernetes events from one instance); Loki as a Grafana datasource.
3. **More metrics:** CloudNativePG PodMonitors (`monitoring.enablePodMonitor` on each Cluster),
   Traefik, Flux, cert-manager, Garage, the Proxmox hosts (`prometheus-pve-exporter` with
   the read-only token), the Pi-holes.
4. **Logs from outside the cluster:** Alloy on the Proxmox hosts, the Pi-holes and the Docker
   VM through `ansible/baseline.yml` (journald and Docker), pushing to Loki through a
   private route.
5. **Alerting:** Alertmanager's receivers; the control-plane metrics bound to the node address
   (kubeadm, through Ansible) and their rules on; a dead-man's switch outside the cluster.
6. **Grafana login through Authentik** (a blueprint and its client secret).
7. **Convert the three releases to captured** (Settled, above).
8. **Log search tuning:** labels kept few (namespace, app, node, stream), the rest as
   structured metadata; saved queries and dashboards for the common questions.

## Checklist

- [x] Phase 1 built
- [ ] Phase 1 live: every target up, Grafana reachable, memory as budgeted
- [ ] Phase 2
- [ ] Phase 3
- [ ] Phase 4
- [ ] Phase 5
- [ ] Phase 6
- [ ] Phase 7
- [ ] Phase 8

## Resuming

`flux get kustomizations -n flux-system | grep -e monitoring -e loki -e alloy -e kube-prometheus`
shows what is deployed; the first unticked box is next. Each phase works without the later
ones.

## Done when

Every phase is ticked, the three releases are captured, and this file moves to `old-queues/`.
