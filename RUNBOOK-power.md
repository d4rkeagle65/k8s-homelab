# Powering everything off and on

For moving the rack, or anything else that turns the whole lab off. The order is the point:
each thing goes down after whatever depends on it, and comes up before it.

Run it from your PC, not from Semaphore: Semaphore lives on the Docker VM, which goes down
partway through. The commands are PowerShell 7 (`pwsh`; Windows PowerShell 5.1's
`ConvertFrom-Json` can't read the whole pod list), with `kubectl` and `flux` pointed at the
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

1. [ ] **Cordon every worker**, so pods from a stopped worker wait as Pending instead of
   piling onto the ones still running:

   ```powershell
   kubectl get nodes -l '!node-role.kubernetes.io/control-plane' -o name | ForEach-Object { kubectl cordon $_ }
   ```

2. [ ] **The Kubernetes workers.** In the Proxmox UI, **Shutdown** (never Stop) every worker
   VM, all within a few seconds of each other. Talos stops its pods cleanly; a database
   instance can take up to three minutes (`smartShutdownTimeout`). A database failover or
   two, and alerts, are expected now. Wait until every worker shows as stopped.
3. [ ] **The control plane.** Shutdown the three control-plane VMs, all at once if you like.
   Talos cordons and drains each first, and with every worker gone the drain can't finish:
   a control-plane node that stays `Ready,SchedulingDisabled` for more than a minute or two
   is waiting on a pod whose disruption budget allows no eviction (Calico's `calico-typha`
   and `calico-apiserver`). Delete what's left on it, which a budget doesn't stop:

   ```powershell
   kubectl get pods -A --field-selector spec.nodeName=<node> -o wide
   kubectl -n calico-system delete pod <pod> --wait=false
   ```

   It then shuts down within seconds. Wait until all three are stopped; the API stops
   answering partway, as it should.
4. [ ] **The other guests:** the Docker VM (Semaphore, Kea, Vaultwarden, Dockhand,
   Tailscale), the domain controller VM, and the remaining containers, then **the two
   Pi-holes last**, since they are the house's DNS. A host's **Bulk Actions → Bulk Shutdown**
   does all of one host's guests at once.
5. [ ] **The Proxmox hosts**, one at a time: the host's **Shutdown** button, or
   `shutdown -h now` in its shell. Once three are off the rest lose quorum and show errors;
   that's expected and harmless now.
6. [ ] **The NAS boxes**, from their own admin pages: the main NAS (app data, block storage,
   the HA guests' disks) and the backup NAS (Garage, the Proxmox backups). They go only now
   because everything above was using them.
7. [ ] **The network last:** the Raspberry Pi, the firewall, the switches, the access points.

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
5. [ ] **The nodes are up:** `kubectl get nodes` shows every node `Ready` (the workers also
   `SchedulingDisabled`, until the next step).
6. [ ] **Uncordon the workers**: cordoning outlasts the power cut, so until now nothing new
   has been scheduled on them:

   ```powershell
   kubectl get nodes -l '!node-role.kubernetes.io/control-plane' -o name | ForEach-Object { kubectl uncordon $_ }
   ```

7. [ ] **Let Flux go now, not last.** Flux's source-controller loses its copy of the repo
   when it restarts, and while the source is suspended it doesn't fetch it again: every
   Kustomization sits at "Fetching manifests", and none reconciles until it's resumed.
   Whatever was merged since the hold deploys now, so look first
   (`git log --oneline <held revision>..origin/main`, the held revision from
   `flux get sources git flux-system`):

   ```powershell
   flux resume source git flux-system
   ```

8. [ ] **The databases recover.** Each one whose primary moved during the shutdown rebuilds a
   replica from its Garage backup, showing `Waiting for the instances` for a few minutes;
   that needs nothing from you. Watch until every one says `Cluster in healthy state`:

   ```powershell
   kubectl get clusters.postgresql.cnpg.io -A
   ```

   Only if one stays unhealthy for more than fifteen minutes, look at its instances and the
   log of the one not ready:

   ```powershell
   kubectl -n <namespace> get pods -l cnpg.io/cluster=<cluster>
   kubectl -n <namespace> logs <instance pod> -c postgres --tail 50
   ```

   An instance that is crash-looping can be deleted (`kubectl -n <namespace> delete pod
   <instance pod>`): the operator makes it again on the same disk. Never delete its PVC.
9. [ ] **Restart the apps stuck behind them.** An app that started while its database or NAS
   was still coming up can sit failing: some wait out a crash-loop back-off (Authentik's
   server, Immich, Linkwarden, n8n), and the Servarr apps (Sonarr, Prowlarr, Bookshelf and the
   like) hang after "Error creating main database" without exiting. Once every database is
   healthy, give them five minutes, then list what's still crash-looping or running but not
   ready, leaving the databases out:

   ```powershell
   $stuck = (kubectl get pods -A -l '!cnpg.io/cluster' -o json | ConvertFrom-Json).items | Where-Object {
     $_.status.containerStatuses | Where-Object {
       $_.state.waiting.reason -eq 'CrashLoopBackOff' -or ($_.state.running -and -not $_.ready) } }
   $stuck | ForEach-Object { "$($_.metadata.namespace)/$($_.metadata.name)" }
   ```

   Then restart them (each Deployment makes a fresh pod):

   ```powershell
   $stuck | ForEach-Object { kubectl -n $_.metadata.namespace delete pod $_.metadata.name --wait=false }
   ```

   A minute or two later, the first command should list nothing, and
   `kubectl get pods -A | Select-String -NotMatch 'Running|Completed'` only its header and
   any old failed Job pods, which are history, not faults.
10. [ ] **Spot-check:** Homepage loads, Authentik signs you in, and one app per database
   answers.
