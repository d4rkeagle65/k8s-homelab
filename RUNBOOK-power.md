# Powering everything off and on

For moving the rack, or anything else that turns the whole lab off. The order is the point:
each thing goes down after whatever depends on it, and comes up before it.

Run it from your PC, not from Semaphore: Semaphore lives on the Docker VM, which goes down
partway through. The commands are PowerShell, with `kubectl` and `flux` pointed at the
cluster.

While it's off, the house has no DNS (the Pi-holes), no DHCP (Kea) and, once the firewall is
off, no internet. Pick a quiet time.

## Before

- [ ] **Backups are recent.** Every database archives its changes to Garage continuously,
  and takes a base backup nightly; `kubectl get backups.postgresql.cnpg.io -A` lists the
  latest, and each should say `completed`. Check the last Proxmox backup job succeeded too.
- [ ] **Nothing is mid-change:** no pull request half-deployed, no Semaphore task running.
- [ ] **Hold Flux on what it has**, so nothing merged meanwhile deploys onto a half-stopped
  cluster (this is what the reboot playbooks do, `ansible/tasks/flux-freeze.yml`):

  ```powershell
  flux suspend source git flux-system
  ```

- [ ] **Note which guests Proxmox HA keeps running.** Shutting an HA guest down sets its HA
  state to stopped, and it stays stopped after the power comes back until you set it to
  started again (step 3 of power-on). On any Proxmox host's shell:

  ```sh
  ha-manager status
  ```

  Keep the list of `service vm:…` and `service ct:…` lines whose state is `started`.

## Power off

1. [ ] **The Kubernetes workers.** In the Proxmox UI, **Shutdown** (never Stop) each worker
   VM. Talos stops its pods cleanly; a database instance can take up to three minutes
   (`smartShutdownTimeout`). Wait until every worker shows as stopped.
2. [ ] **The control plane.** Shutdown the three control-plane VMs, all at once if you like.
   Wait until they're stopped.
3. [ ] **The other guests:** the Docker VM (Semaphore, Kea, Vaultwarden, Dockhand,
   Tailscale), the domain controller VM, and the remaining containers, then **the two
   Pi-holes last**, since they are the house's DNS. A host's **Bulk Actions → Bulk Shutdown**
   does all of one host's guests at once.
4. [ ] **The Proxmox hosts**, one at a time: the host's **Shutdown** button, or
   `shutdown -h now` in its shell. Once three are off the rest lose quorum and show errors;
   that's expected and harmless now.
5. [ ] **The NAS boxes**, from their own admin pages: the main NAS (app data, block storage,
   the HA guests' disks) and the backup NAS (Garage, the Proxmox backups). They go only now
   because everything above was using them.
6. [ ] **The network last:** the Raspberry Pi, the firewall, the switches, the access points.

Everything is off; unplug and move.

## Power on

1. [ ] **The network:** switches, firewall, access points, the Raspberry Pi. Wait until the
   switches' lights settle and the firewall has routed (a few minutes).
2. [ ] **The NAS boxes**, and wait until each is fully up: its admin page loads and its
   shares are mounted. The main NAS holds the HA guests' disks and the cluster's files; on the
   backup NAS, Garage should be running (`docker ps` shows it). A cluster that starts before
   them leaves pods failing that need a restart later.
3. [ ] **The Proxmox hosts, all within a minute or two.** A host starts its guests only once
   the cluster has quorum (three of five), so don't wait between them. The Talos VMs, the
   Pi-holes and the Docker VM start on their own.
   - Set the HA guests you noted back to started, each with
     `ha-manager set <service> --state started` (e.g. `vm:100`), on any host's shell.
     `ha-manager status` should list them all `started` again.
4. [ ] **DNS and DHCP:** a device on the network gets an address, and a name resolves.
5. [ ] **The cluster is whole:**

   ```powershell
   kubectl get nodes
   kubectl get clusters.postgresql.cnpg.io -A
   kubectl get pods -A | Select-String -NotMatch 'Running|Completed'
   ```

   Every node `Ready`, every database `Cluster in healthy state`. The databases can take a
   few minutes after the nodes.
6. [ ] **Let Flux go:**

   ```powershell
   flux resume source git flux-system
   ```

7. [ ] **Restart what's stuck.** An app that started while its database or NAS was still
   coming up can sit failing: Authentik's server waits out a crash-loop back-off, and
   Sonarr-style apps hang after "Error creating main database" without exiting. Restart each
   with `kubectl -n <namespace> delete pod <pod>`; its Deployment makes a fresh one. Then the
   last command of step 5 should print nothing but its header.
8. [ ] **Spot-check:** Homepage loads, Authentik signs you in, and one app per database
   answers.
