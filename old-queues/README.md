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
