# Ansible playbooks

Run from Semaphore on the Docker host (`docker/semaphore/`), which pulls this folder from git.
What's specific to one site lives only in Semaphore, never here: the inventory (host names
and addresses), the SSH key, and the Kubernetes token.

| Playbook | What it does |
|---|---|
| `proxmox-upgrade.yml` | Upgrades the Proxmox hosts one at a time (`apt dist-upgrade`, `autoremove`), rebooting only when a new kernel or Debian asks for it. Before each host and after any reboot it checks the Proxmox cluster has quorum and every Kubernetes node is Ready, and stops the run if not. It live-migrates the VMs listed in `evacuate` off a host before rebooting it and back afterwards, and, with two Pi-holes or more, never lets DNS go fully down: a host carrying one reboots only while the others answer. Hosts in `no_reboot` are upgraded but left for you to reboot. |
| `k8s-node-update.yml` | Routine OS updates of the Kubernetes node containers, one at a time: drains the node (switching each database primary on it over to a replica elsewhere first), `apt dist-upgrade`, reboots the container when anything was upgraded, uncordons, and waits for the node and every database cluster before the next. kubeadm, kubelet, kubectl and cri-o stay held; Kubernetes version upgrades are a separate job (`QUEUE-k8s-upgrade.md`). It also owns the nodes' Kubernetes and CRI-O package sources. |
| `k8s-node-reboot.yml` | Reboots the Kubernetes node containers one at a time, changing nothing else: the same checks, database switchovers, drain and wait as `k8s-node-update.yml`, with a reboot in place of the upgrade. |
| `k8s-upgrade.yml` | Moves Kubernetes to one bundle from `k8s-bundles.yml` (`k8s_bundle`): this minor's latest patch or the next minor, with CRI-O, etcd, kube-vip and the pause image to match. It first checks the bundle against the live cluster and stops on anything that fails or can't be answered. |
| `pihole-update.yml` | Updates the Pi-holes one at a time (`apt dist-upgrade`, then `pihole -up`), each only while every other one answers DNS, and waits for it to answer again before the next. With a single Pi-hole it stops unless `allow_dns_outage: true`. Not yet run. |
| `docker-vm-update.yml` | Updates the Docker VM's packages with Docker held, and reports when a reboot or a Docker upgrade is due; it never does either, since Semaphore runs there. Not yet run. |

## Setting up Semaphore (once)

Everything below is in Semaphore's web UI, in one project: create it first from the project
menu at the top left, **New Project** (e.g. "Homelab", **Demo** off). Key Store, Inventory,
Repositories and the rest are in that project's sidebar, not on the admin pages.

1. **An SSH key of its own for the Proxmox hosts.** On the PC, make a key pair:

   ```powershell
   ssh-keygen -t ed25519 -C semaphore -f $env:USERPROFILE\.ssh\semaphore_proxmox
   ```

   Add the `.pub` line to `/root/.ssh/authorized_keys` on each Proxmox host. In Semaphore,
   **Key Store** > New Key, type SSH Key, user `root`, paste the private key. Then delete the
   private key from the PC (Semaphore holds it, encrypted), or keep it in Vaultwarden.
   The hosts' own keys are pinned in `/opt/semaphore/config/known_hosts` on the Docker host
   (`docker/semaphore/README.md`).

2. **The repository.** **Repositories** > New: URL
   `https://github.com/d4rkeagle65/k8s-homelab.git`, branch `main`, access key **None** (the
   repo is public).

3. **The inventory.** **Inventory** > New, type **Static YAML**, user credentials the key from
   step 1, with the hosts in the order to upgrade them:

   ```yaml
   proxmox:
     hosts:
       <host 1>: {ansible_host: <management address>}
       <host 2>: {ansible_host: <management address>}
       <host 3>: {ansible_host: <management address>}
       <host 4>:
         ansible_host: <management address>
         evacuate:
           - {vmid: <Docker VM's id>, to: <host with room for it>}
     vars:
       ansible_user: root
       dns_guest_tag: <the Proxmox tag on the Pi-hole containers>
   ```

   - **`evacuate`**: VMs to live-migrate off the host before its reboot, each to another host
     in the inventory, and back afterwards (`evacuate_return: false` leaves them there). The
     Docker VM needs this, since Semaphore runs on it; put its host last. Each run checks the
     target has the VM's memory plus 1 GB to spare (`evacuate_memory_margin`, in MB) and its
     bridges, and stops before touching the host if not. Disks on local storage are copied
     across, which takes a few minutes. Only VMs: a container can't move while running.
   - **`dns_guest_tag`**: the playbook finds the Pi-holes by this tag, so a second one is
     covered once it has the tag. A host carrying one reboots only while every other answers
     (a lookup of `pi.hole`, or `dns_check_name`), and the run waits for its own to answer
     again before the next host. A host carrying all of them (two or more) is left for you to
     reboot. With one Pi-hole, DNS is down while its host reboots, and the run's report says
     so. Unset, DNS isn't checked.
   - **`remove_old_kernels`** (optional, default off): purge old kernels, keeping the running
     one, the newest `keep_kernels` (default 2) and any pinned in `proxmox-boot-tool`. Every
     run lists what it would remove either way; `/boot` is only 456 MB on these hosts.
   - **`no_reboot`** (a group, optional): hosts to upgrade but never reboot, for example the
     Docker VM's host if there's nowhere to move the VM.

4. **The Kubernetes access.** Semaphore reads the cluster through the read-only
   `cluster-access/semaphore` ServiceAccount (`kubernetes/prod/system/cluster-access/`).
   **Variable Groups** > New, "Kubernetes", all three as **extra variables** (Ansible
   variables, not environment variables):
   - `k8s_api_url`: the API server URL, the same as in your kubeconfig: `https://`, the
     control plane's name and `:6443`, a name its certificate covers. On the PC:

     ```powershell
     kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'
     ```

   - `k8s_ca_b64` (a secret): the cluster's CA certificate in base64, one line, as the Secret
     stores it (Semaphore's secret fields are one line, and drop a certificate's line breaks).
     On the PC:

     ```powershell
     kubectl -n cluster-access get secret semaphore-token -o jsonpath='{.data.ca\.crt}' | Set-Clipboard
     ```

   - `k8s_token` (a secret): the ServiceAccount's token. On the PC:

     ```powershell
     [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String((kubectl -n cluster-access get secret semaphore-token -o jsonpath='{.data.token}'))) | Set-Clipboard
     ```

   Clear the clipboard afterwards (`Set-Clipboard -Value ' '`).

5. **Two task templates**, both with playbook `ansible/proxmox-upgrade.yml`, the inventory
   from step 3, the repository from step 2 and the "Kubernetes" variable group:
   - **"Proxmox upgrade: check"**, with CLI args `["--check"]`: runs the checks and lists the
     pending upgrades, changing nothing. Safe to schedule (e.g. weekly).
   - **"Proxmox upgrade"**: the real run. Start it by hand.

## The Kubernetes nodes (`k8s-node-update.yml`)

Set up once, after the Proxmox hosts:

1. **An SSH key of its own for the nodes**, made like the hosts' (step 1 above), in the Key
   Store as its own key. Add its `.pub` line to `/root/.ssh/authorized_keys` in each node
   container; from the Proxmox host a container is on:
   `pct exec <id> -- sh -c 'mkdir -p -m 700 /root/.ssh && echo "<the .pub line>" >> /root/.ssh/authorized_keys'`.
   Only `ssh.service` should serve SSH there: with `ssh.socket` enabled too, systemd holds
   port 22 and `sshd` fails on its next reload (`systemctl disable --now ssh.socket`).
2. **Pin the nodes' host keys**, on the Docker host, with their management addresses (as for
   the hosts in `docker/semaphore/README.md`, appending):

   ```sh
   ssh-keyscan -t ed25519 <node 1> <node 2> ... >> /opt/semaphore/config/known_hosts
   ```

3. **The inventory:** a second one, "Kubernetes nodes", type Static YAML, with the key from
   step 1 (an inventory has one key). Each node is named exactly as its Kubernetes node, by
   management address:

   ```yaml
   k8s_nodes:
     children:
       k8s_control_planes:
         hosts:
           <control plane 1>: {ansible_host: <management address>}
           ...
       k8s_workers:
         hosts:
           <worker 1>: {ansible_host: <management address>}
           ...
     vars:
       ansible_user: root
       ansible_python_interpreter: /usr/bin/python3
   ```

4. **Two task templates**, playbook `ansible/k8s-node-update.yml`, the "Kubernetes nodes"
   inventory, the repository and the "Kubernetes" variable group: **"Kubernetes nodes: check"** with CLI args
   `["--check"]`, and **"Kubernetes nodes: update"**.

**Rebooting the nodes** (`k8s-node-reboot.yml`): two more templates on the same inventory,
repository and variable group, **"Kubernetes nodes: reboot check"** (`["--check"]`) and
**"Kubernetes nodes: reboot"**. To reboot only some nodes, set the template's (or the run's)
limit to those nodes plus `localhost`, e.g. `<node>,localhost`; without `localhost`
the run stops at its first check, having changed nothing.

What a run does to the workloads: each database cluster with two instances keeps running
(its primary is switched over before the drain, and its replica waits for the node); a
single-instance one (immich, manictime) is down while its node updates. If a run stops
partway, the node it was on stays cordoned: fix the cause, then run it again, or
`kubectl uncordon` it.

## Kubernetes version upgrades (`k8s-upgrade.yml`)

A **bundle** in `k8s-bundles.yml` is one Kubernetes minor with everything that has to match
it: the exact kubeadm, kubelet, kubectl and CRI-O packages, the pause image, kube-vip, an etcd
override where one is needed, and the version range each add-on must be in. A run installs one
bundle, chosen with `k8s_bundle`.

Before it touches anything, a run stops unless:

- the bundle is the cluster's current minor or the next one, and not older;
- every node's kubelet is within the skew the target allows, with CRI-O on the kubelet's minor;
- etcd is new enough (etcd 3.6 needs every member on 3.5.32 or later first);
- the bundle's packages exist in their repositories, and its etcd and kube-vip images exist;
- no API the target removes is in use (each API server's own count of deprecated requests);
- every add-on runs a version inside the bundle's range, and every HelmRelease is either an
  add-on or listed as version-independent: an unknown counts as incompatible;
- every node runs cgroup v2.

Add-ons that Flux installs are upgraded by commits first; the checks say which. Then a run
upgrades the control planes one at a time, then the workers, each drained like a node update.
A node already on the bundle isn't drained again, so a run that stopped can be run again.

**On LXC nodes,** kubeadm's SystemVerification preflight check can't read the host's kernel
config and fails; add `kubeadm_ignore_preflight_errors: [SystemVerification]` to the
"Kubernetes nodes" inventory's `vars:`. Its cgroup part is covered by the check above.

**Templates:** playbook `ansible/k8s-upgrade.yml`, the "Kubernetes nodes" inventory, the
repository and the "Kubernetes" variable group, with `k8s_bundle` in the template's extra
variables (`{"k8s_bundle": "1.33"}`, a string): **"Kubernetes upgrade: check"** with CLI args
`["--check"]` and **"Kubernetes upgrade"**. Change the bundle on both for each step.

**A new bundle:** copy the newest, set the versions, and give every add-on's range from its
project's support page, with the link. A new add-on goes into `k8s_addons` with how to read
its version, and a range in every bundle.

## The Pi-holes and the Docker VM (`pihole-update.yml`, `docker-vm-update.yml`)

Neither has run yet; start each with a check-mode template.

- **Keys and inventories.** Each needs Semaphore's key in the guest's
  `/root/.ssh/authorized_keys` (from its Proxmox host with `pct exec`, as for the nodes, or
  through the VM's console) and an inventory with a `pihole` or a `docker_vm` group, user
  `root`. A separate key per kind of machine keeps each revocable alone; an inventory holds one
  key.
- **Reaching the Docker VM from Semaphore,** which runs in a container on that VM: its
  management address doesn't answer containers on the VM itself (the policy routing that sends
  its replies out the management interface), so give `ansible_host` as the address of the
  Docker network's gateway as Semaphore's container sees it (`ip route` inside the container:
  the `default via` address).
- **Templates:** a check (`["--check"]`) and a real one for each, with the inventory and the
  repository; neither needs the "Kubernetes" variable group.

## Running a Proxmox upgrade

1. Run **"Proxmox upgrade: check"** and read the pending upgrades per host.
2. Run **"Proxmox upgrade"**. It stops at the first failed check, before touching the next
   host; fix the cause and run it again (upgraded hosts have nothing left to do).
3. When it reports a host that still needs a reboot by hand, reboot that host from Proxmox when
   convenient, after moving anything that has to stay up.

## Checks for a playbook here

- `ansible-playbook --syntax-check` passes, with any inventory.
- Anything a playbook reads from a cluster or a host it reads with `changed_when: false` and
  `check_mode: false`, so a check-mode run reports real facts.
- A token or password goes in Semaphore, never in this repo, and a task that sends one carries
  `no_log: true`.
