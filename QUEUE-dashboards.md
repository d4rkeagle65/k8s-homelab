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
- [x] Project dashboards (`monitoring/dashboards/`, made to fit: data source uid `prometheus`,
      `${var}` as `$var`, the rest `$${...}` for Flux): exportarr's Media dashboard and
      qbittorrent-exporter's (Apps), Garage's (Storage), Flux's cluster and control plane
      (Kubernetes; kube-state-metrics makes `gotk_resource_info` from the 10 Flux kinds). Kea's
      12688 fits (only its DHCPv6 panels are empty)
- [x] qBittorrent's dashboard shows data (2026-10-11): its exporter names qBittorrent 5.2's
      cookie (`QBITTORRENT_COOKIE_NAME=QBT_SID_8080`), no API key needed; 17 torrents
- [x] Written here, Drives (`monitoring/dashboards/`, uid `homelab-drives`): NVMe wear, spare,
      temperature and errors; writes per host, per local SSD and per guest; the NAS LUNs apart.
      Local SSDs are the physical drives only (`node_disk_info` path not `ip-*`), so ZFS and LVM
      volumes and the iSCSI LUNs don't count the same writes twice. Every query checked
      against Prometheus.
- [x] Written here (Homelab folder): the overview (what's down, what's near a limit, hosts,
      nodes' memory used and requested, network, storage) and Home Assistant (entities, the
      unavailable ones, updates, state changes, batteries); every query checked. Flux's come
      from the project instead (above)
- [x] Homepage built (`services/homepage/`): v2.4.0 through app-template, its config inline (the
      chart restarts it on a change), its own OIDC login through Authentik (the homepage
      blueprint, authentik Admins only) and only on the private networks, at `home.<domain>`.
      29 services in five groups; widgets for Proxmox, both Pi-holes, Grafana, Sonarr, Radarr,
      Lidarr, Bookshelf, Prowlarr, qBittorrent, Home Assistant and Immich, and the cluster's
      resources. qBittorrent's widget may fail like its exporter (qBittorrent 5.2 wants an API
      key); Immich's uses the Power Tools key, which may lack the statistics permission.
- [x] Homepage laid out in tabs (Systems, Cluster, Apps, Media, Reference) with a quick-links row
      on every tab. Cards with no widget of their own read Prometheus (`prometheusmetric`): the
      Proxmox hosts, the Docker host, Kea, Traefik, both NASes (the snmp-exporter's `nas` label),
      Garage, Kubernetes, Flux, CloudNativePG, Loki. Each cluster app's card shows its pods'
      state, CPU and memory (`namespace` and `app`, or `podSelector`), and the Docker host's apps
      use `siteMonitor`. The calendar reads Sonarr and Radarr. Every query checked against Prometheus.
- [x] Homepage live (2026-10-11): every widget's API call made from inside the Homepage pod with
      its own credentials and addresses: all 18 answer (Proxmox, both Pi-holes, the Cloudflare
      tunnel, Tailscale, DSM, Grafana, Prometheus, Home Assistant, Immich's statistics,
      Grimmory, Tdarr, qBittorrent's login, the five *arrs)
- [x] Homepage's Synology, Cloudflare tunnel and Tailscale widgets and the OpenWrt access
      points' cards work. The Tailscale API key lasts 90 days at most, so its widget fails when
      it expires: renew it in `TAILSCALE_API_KEY` (from about 2027-01-08).
- [x] The switches (2026-10-11): the TL-SG3428 over SNMP (`switch-core`, `switch="core"`, 15
      ports linked), the SNMP Interface Detail dashboard (12492, Network) and a Homepage card; the
      two TL-SG105E (Easy Smart, no SNMP) pinged every minute by the blackbox exporter
      (`monitoring/blackbox-exporter/`, Probes `switch-02`/`switch-04`), on a Homepage card. All
      targets up; Grafana lists every new dashboard in its folder
- [ ] Homepage widgets that need a key the cluster doesn't have yet. Each needs its Vaultwarden
      field, its mapping in cluster-secrets and the `homepage` Secret, pushed before the widget:
      Jellyseerr (`seerr`, its API key), Emby (an API key), SABnzbd (its API key), Mealie (an API
      token), Authentik (a token, read-only use), Dockhand (a user or an API key).

## Resuming

Grafana's dashboards list shows what's loaded; the first unticked box is next.

## Done when

Every box is ticked and this file moves to `old-queues/`.
