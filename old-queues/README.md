# Retired working queues

Each `QUEUE-<task>.md` here covers one finished long job: the ask, the decisions settled, the
checklist, and the checks that came back **negative**. They are archives, moved here unedited:
a claim inside one says what that job decided, not what is true now. Current work is in
`QUEUE.md` and current rules are in `CLAUDE.md`.

Read the negative results before re-proposing anything.

| File | Job | Started | Retired |
|---|---|---|---|
| [QUEUE-apps-layout.md](QUEUE-apps-layout.md) | `kubernetes/apps/` to `kubernetes/prod/<category>/`, labels on every object, babybuddy-mcp into `babybuddy`, the label and annotation audit | 2026-10-05 | 2026-10-05 |
| [QUEUE-semaphore.md](QUEUE-semaphore.md) | Semaphore (Ansible and OpenTofu) on the Docker host with Authentik login; the rolling Proxmox upgrade playbook with quorum and k8s checks, Docker VM evacuation and the two-Pi-hole DNS rule; first real run 2026-10-08 | 2026-10-05 | 2026-10-08 |
| [QUEUE-k8s-upgrade.md](QUEUE-k8s-upgrade.md) | Node update and version-upgrade playbooks with compatibility bundles; Kubernetes 1.33.3 to 1.36.5 (CRI-O off a dev build, etcd 3.5 to 3.6), cert-manager, CloudNativePG, Flux, metrics-server and Calico (adopted into Flux) upgraded alongside | 2026-10-06 | 2026-10-07 |
| [QUEUE-restore-tests.md](QUEUE-restore-tests.md) | Restore tests of the eleven CloudNativePG backups not proven at setup (all but mealie): each recovered from Garage into a one-instance copy and compared table by table with the live database; all pass | 2026-10-09 | 2026-10-09 |
| [QUEUE-grimmory.md](QUEUE-grimmory.md) | An e-book reader over Bookshelf's books: Booklore, then replaced by Grimmory (Booklore in maintenance mode; BookOrbit unsupported on NFS), MariaDB with exporter and nightly dumps (restore-tested), configured from git by a sync Job, Authentik login, Homepage, Organizr and Grafana | 2026-10-11 | 2026-10-11 |
| [QUEUE-dashboards.md](QUEUE-dashboards.md) | Grafana dashboards (14 community, the projects' own for exportarr, qBittorrent, Garage and Flux, and the Homelab overview, Drives and Home Assistant written here) and Homepage (tabs, quick links, a widget or Prometheus card for every service, the switches by SNMP and ping), every widget's API tested | 2026-10-10 | 2026-10-11 |
| [QUEUE-app-template.md](QUEUE-app-template.md) | The bjw-s app-template chart 3.7.3 to 5.3.0 on all 16 releases and Jackett: each Deployment and Grimmory's MariaDB StatefulSet recreated (an immutable selector label changed; `upgrade.force` doesn't help), Homepage's ServiceAccount kept with its token; a failed upgrade had already moved the Service, so each app was down until recreated | 2026-10-15 | 2026-10-15 |
| [QUEUE-terraform.md](QUEUE-terraform.md) | OpenTofu from Semaphore with state in its PostgreSQL: the Proxmox guests imported (k8s LXCs, Pi-holes, Docker VM), then the Talos VMs built by it, and Cloudflare's hand-made DNS records (`tofu/cloudflare`) | 2026-10-07 | 2026-10-16 |
| [QUEUE-organizr.md](QUEUE-organizr.md) | Organizr configured from git: the `organizr-sync` Job applying `organizr.yaml` through its API (45 settings, 5 categories, 28 tabs, read back), Authentik login with `#noredirect` as the way in, the *arr apps framed (their `TrustedNetworks` set, so a lapsed session's login redirect is https), `Referrer-Policy: same-origin` for qBittorrent's CSRF check, and the homepage's media, download, Emby and Seerr items | 2026-10-11 | 2026-10-18 |
