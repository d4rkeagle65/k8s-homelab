# Dashboards: Grafana and Homepage

**The ask:** Grafana dashboards over everything Prometheus and Loki collect
(`QUEUE-monitoring.md`), and a Homepage start page for the homelab's services.

## Settled

- **Dashboards are code.** Community dashboards come from grafana.com by ID and a pinned
  revision, listed in the kube-prometheus-stack HelmRelease (`grafana.dashboards`); Grafana's
  init container downloads them at start. Dashboards written here, or copied from a project's
  own repository, are JSON in ConfigMaps labelled `grafana_dashboard`, picked up by Grafana's
  sidecar (it searches every namespace). A dashboard edited in Grafana's UI is lost on the
  next start unless it's copied back into git.
- **Data sources by UID:** each dashboard's inputs (`DS_PROMETHEUS`, `DS_LOKI`) map to the
  fixed UIDs `prometheus` and `loki`.
- **Folders:** Hosts (Proxmox, node-exporter, drives), Kubernetes, Network (Traefik, Pi-hole,
  Kea, cert-manager), Storage (the NASes, Garage), Apps (media, Home Assistant, qBittorrent),
  Logs, and Homelab for the dashboards written here.
- **Chosen community dashboards** (revision pinned): Node Exporter Full 1860, Proxmox via
  Prometheus 10347, Kubernetes Views 15757-15761, Traefik 17346, Pi-hole Exporter 10176,
  Synology NAS Details 14284, cert-manager 20842, cAdvisor 19792, Kubernetes Loki logs 16976,
  Kea DHCP 12688. Left out: Flux 16714 (2022, metrics Flux removed), Home Assistant's (old or
  InfluxDB), the *arr ones on grafana.com (2020; exportarr's repository has current ones).
- **Homepage:** in the cluster through app-template (as n8n), its services from git, Kubernetes
  discovery from Ingress annotations, widgets for the services that have them, behind
  `private-networks` and the Authentik login.

## Checklist

- [x] Community dashboards, in folders (14; the Synology target also polls the generic
      `ucd_*` profiles the Synology dashboard selects by)
- [ ] Project dashboards: exportarr, Garage, Kea/Stork (if 12688 doesn't fit), qBittorrent
      (once its exporter works: `QBITTORRENT_API_KEY`)
- [x] Written here, Drives (`monitoring/dashboards/`, uid `homelab-drives`): NVMe wear, spare,
      temperature and errors; writes per host, per local SSD and per guest; the NAS LUNs apart.
      Local SSDs are the physical drives only (`node_disk_info` path not `ip-*`), so ZFS and LVM
      volumes and the iSCSI LUNs don't count the same writes twice. Every query checked
      against Prometheus.
- [ ] Written here: Homelab overview; Flux; Home Assistant
- [x] Homepage built (`services/homepage/`): v2.4.0 through app-template, its config inline (the
      chart restarts it on a change), its own OIDC login through Authentik (the homepage
      blueprint, authentik Admins only) and only on the private networks, at `home.<domain>`.
      29 services in five groups; widgets for Proxmox, both Pi-holes, Grafana, Sonarr, Radarr,
      Lidarr, Bookshelf, Prowlarr, qBittorrent, Home Assistant and Immich, and the cluster's
      resources. qBittorrent's widget may fail like its exporter (qBittorrent 5.2 wants an API
      key); Immich's uses the Power Tools key, which may lack the statistics permission.
- [ ] Homepage live: every widget shows data

## Resuming

Grafana's dashboards list shows what's loaded; the first unticked box is next.

## Done when

Every box is ticked and this file moves to `old-queues/`.
