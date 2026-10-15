# Working in docker/

Each `docker/<stack>/` is deployed by Dockhand as a Git stack, from its `compose.yaml`: to the
Docker host, or, for `garage`, to the NAS through its Hawser agent. Flux never reads this
folder. A stack deployed through Hawser runs on that machine's Docker, so its host paths are
paths there: it can't bind-mount a file from this folder, so a small config goes inline
(`configs:` with `content:`, as `garage/compose.yaml` does). Dockhand's own compose file isn't here: it lives
on the host, so Dockhand never manages itself.

When `main` moves, Flux calls each stack's webhook in Dockhand
(`kubernetes/prod/system/external-services/dockhand-webhooks/`), and Dockhand redeploys the
stacks whose folder changed. `claude-code` has none: it redeploys on a weekly schedule, since
a redeploy restarts the session running in it. A new stack gets its webhook turned on in
Dockhand (its secret is the Vaultwarden field `DOCKHAND_WEBHOOK_SECRET`) and a Provider and Alert in that folder. A service
built here (`build:`) carries `pull_policy: build` and a `local` tag, or a redeploy tries to pull it.

- **A stack's real values are `${VARIABLE}`s set in Dockhand, and its data lives on the
  host.** Credentials are marked secret there. Data stays in bind mounts on the host, never
  in git, and so does config that's mostly addresses (Kea's subnets and reservations; see
  `kea/README.md`).
- **On the Docker host, reach a published port through `127.0.0.1`, never the host's
  management address.** From the host itself, and from its host-network containers
  (`claude-code`), a connection to that address leaves from it, and the management routing
  rules (table 250) send it out to the network instead of to the container. Publish the port
  on loopback too, as `kea/compose.yaml` does for Kea's API.
