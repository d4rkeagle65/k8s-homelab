# Zammad: a helpdesk where alerts become tickets

**The ask:** the owner chose Zammad for tickets (self-hosted, AGPL, every feature free), and on
2026-10-16 asked to deploy it. Its use here: alerts from Alertmanager open tickets, follow-ups
and recoveries update them, and anything else worth tracking gets a ticket too.

## Settled

- **Zammad's own chart** (`zammad.github.io/zammad-helm`, 19.1.5 with Zammad 7.2.2), in
  `prod/apps/zammad/`, handwritten.
- **Its database on CloudNativePG** (`zammad-db`, PostgreSQL 18 on trixie, two instances,
  nightly backups to Garage), not the chart's PostgreSQL. Zammad builds its connection URL
  from the password without escaping it, so the password must need no URL-encoding:
  CloudNativePG's generated one is checked to be letters and digits at creation. The chart's
  own Redis (no volume: sessions only) and Memcached.
- **Login through Authentik** by OIDC, a public client with PKCE (no client secret), allowed for
  authentik Admins. The first admin and the OIDC settings come from the chart's auto wizard
  (`autoWizard` in `helmrelease.yaml`), applied once at the first visit; the admin's email is the owner's
  Authentik email (`ZAMMAD_ADMIN_EMAIL`), so the first Authentik login links to it.
- **Search is Elasticsearch,** the chart's bundled node (`eck-elasticsearch`), run by Elastic's
  ECK operator (`prod/system/elastic-system/eck-operator`, managing only this namespace).

## Checklist

- [x] Deployed (2026-10-16): the variables first, then the app; every pod Ready (the Rails
      server, scheduler and websocket restarted two or three times while the database and
      migrations came up), the init Job's migrations done, `zammad-db`'s password 64 letters
      and digits, its first backup completed, the web UI 200 with its certificate
- [ ] The first visit runs the auto wizard: the admin exists, `fqdn` and `http_type` set, OIDC
      on; the owner logs in through Authentik and lands on the admin account (not a new
      customer)
- [x] Homepage's tile (Tools, with a site monitor) and Organizr's tab (Infrastructure, admins), 2026-10-16
- [x] Alerts into tickets (2026-10-16): Alertmanager's route sends `warning` and `critical`
      (never `Watchdog`, `InfoInhibitor` or `info`), each alert on its own, to
      `monitoring/zammad-bridge`, which posts it to Zammad's Checkmk endpoint
      (`ZAMMAD_ALERT_WEBHOOK_URL`, group "Alerts"): a ticket per alert titled "<alert> <target> is
      <STATE>", a note on each repeat (12 hours), closed on recovery. Proved against Zammad
      before the deploy: open, note, close (ticket 3, "BridgeTest")
- [ ] A group alert's ticket names its members (owner's ask, 2026-10-16): for `TargetDown`,
      `AlertmanagerClusterDown` and the Deployment, StatefulSet and DaemonSet alerts the bridge
      asks Prometheus which are down at that moment (`MEMBERS` in `zammad-bridge.py`) and lists
      them with node and address. Done when a real one has fired and its ticket shows the list
- [ ] Mail: Zammad's outgoing mail through `smtp-oauth-relay` (notifications), and an incoming
      channel if tickets by email are wanted (the owner's call)
- [ ] Elasticsearch through the ECK operator (2026-10-16, pushed): one 9.5 node, 512 MiB heap
      in 1 GiB, 5 GiB on `nas-iscsi`; done when it's green and a search in Zammad finds a ticket
- [ ] Resources re-measured after a week (`QUEUE-resource-limits.md`'s rules); its database
      added to the restore tests' method

## Resuming

`kubectl -n zammad get pods,jobs` and the HelmRelease's status; the first unticked box is next.

## Done when

Alerts arrive as tickets and close on recovery, the owner logs in through Authentik, and every
box is ticked; then this file moves to `old-queues/`.

## Checked and negative

- No Alertmanager integration ships with Zammad; its Prometheus-related docs cover Zammad's own
  metrics, not alerts in.
