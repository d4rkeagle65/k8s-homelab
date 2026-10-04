# Working in docker/

Each `docker/<stack>/` is deployed to the Docker host by Dockhand as a Git stack, from its
`compose.yaml`. Flux never reads this folder.

- **A stack's real values are `${VARIABLE}`s set in its environment in Dockhand**, with
  credentials marked secret, as the no-real-values rule needs everywhere.
- **Data stays in bind mounts on the host, never in git**, and so does config that's mostly
  addresses (Kea's subnets and reservations; see `kea/README.md`).
- **Dockhand's own compose file isn't here.** It lives on the host, so Dockhand never manages
  itself.
- **On the Docker host, reach a published port through `127.0.0.1`, never the host's
  management address.** From the host itself, and from its host-network containers
  (`claude-code`), a connection to that address leaves from it, and the management routing
  rules (table 250) send it out to the network instead of to the container. Publish the port
  on loopback too, as `kea/compose.yaml` does for Kea's API.
