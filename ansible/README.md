# Ansible playbooks

Run from Semaphore on the Docker host (`docker/semaphore/`), which pulls this folder from git.
What's specific to one site lives only in Semaphore, never here: the inventory (host names
and addresses), the SSH key, and the Kubernetes token.

| Playbook | What it does |
|---|---|
| `proxmox-upgrade.yml` | Upgrades the Proxmox hosts one at a time (`apt dist-upgrade`, `autoremove`), rebooting only when a new kernel or Debian asks for it. Before each host and after any reboot it checks the Proxmox cluster has quorum and every Kubernetes node is Ready, and stops the run if not. It live-migrates the VMs listed in `evacuate` off a host before rebooting it and back afterwards, and, with two Pi-holes or more, never lets DNS go fully down: a host carrying one reboots only while the others answer. Hosts in `no_reboot` are upgraded but left for you to reboot. |
| `proxmox-host.yml` | The Proxmox hosts' own settings, on every host at once (no reboots, no guests touched): the ZFS ARC cap (`zfs_arc_max_gib`, default 10% of RAM, at least 2, into `/etc/modprobe.d/zfs.conf`, the initramfs when root is on ZFS, and the running module, read back from the ARC's statistics), and a trusted certificate for each host's web UI and API (`tasks/proxmox-acme.yml`: Let's Encrypt through Proxmox's ACME client and Cloudflare DNS, ordered when missing or within 30 days of expiry, then read back). Each host's own iSCSI LUN on the NAS as storage `nas-block` (`tasks/proxmox-nas-block.yml`, off until set). A dry run reports each host's ARC, certificate and NAS block storage. |
| `k8s-node-update.yml` | Routine OS updates of the Kubernetes node containers, one at a time: drains the node (switching each database primary on it over to a replica elsewhere first), `apt dist-upgrade`, reboots the container when anything was upgraded, uncordons, and waits for the node and every database cluster before the next. kubeadm, kubelet, kubectl and cri-o stay held; Kubernetes version upgrades are `k8s-upgrade.yml`. It also owns the nodes' Kubernetes and CRI-O package sources. |
| `k8s-node-reboot.yml` | Reboots the Kubernetes node containers one at a time, changing nothing else: the same checks, database switchovers, drain and wait as `k8s-node-update.yml`, with a reboot in place of the upgrade. |
| `k8s-upgrade.yml` | Moves Kubernetes to one bundle from `k8s-bundles.yml` (`k8s_bundle`): this minor's latest patch or the next minor, with CRI-O, etcd, kube-vip and the pause image to match. It first checks the bundle against the live cluster and stops on anything that fails or can't be answered. |
| `pihole-update.yml` | Updates the Pi-holes one at a time (`apt dist-upgrade`, then `pihole -up`), each only while every other one answers DNS, and waits for it to answer again before the next. With a single Pi-hole it stops unless `allow_dns_outage: true`. Not yet run. |
| `pihole-config.yml` | Sets every Pi-hole's `pihole-FTL` settings to the values in `pihole_settings` (a Semaphore variable group), one at a time and only while the others answer DNS: sets each that differs, reads it back, restarts FTL and waits for it to answer again. It refuses settings nebula-sync copies from Pi-hole 1, so each setting has one owner. Check mode reports the differences. Not yet run. |
| `baseline.yml` | The same baseline on every Debian host at once, from the "All Debian Hosts" inventory: the admin login (`admin-users.yml`'s steps), Semaphore's shared key for root, the packages in `vars/baseline.yml` (installed when missing, never removed or upgraded), and one interactive bash setup (`files/homelab-bashrc`). Check mode reports each part. Not yet run. |
| `admin-users.yml` | The admin login alone, on every host of the inventory it runs against (`baseline.yml` runs the same steps, `tasks/admin-user.yml`): the account `admin_user` with exactly `admin_ssh_keys` in its `authorized_keys`, `sudo` installed, and sudo through Debian's `sudo` group (passwordless with `admin_sudo_nopasswd: true`). It checks the result the way sudo sees it (`sudo -l -U`), so a group sudoers doesn't grant fails the run. Root's logins and `sshd` are left alone. |
| `docker-vm-update.yml` | Updates the Docker VM's packages with Docker held, and reports when a reboot or a Docker upgrade is due; it never does either, since Semaphore runs there. Not yet run. |

## Setting up Semaphore (once)

Everything below is in Semaphore's web UI, in one project: create it first from the project
menu at the top left, **New Project** (e.g. "Homelab", **Demo** off). Key Store, Inventory,
Repositories and the rest are in that project's sidebar, not on the admin pages.

**One template per playbook.** A check run is the same template with **Dry run** ticked in the
run dialog (`--check`; **Diff** adds `--diff`); every playbook here supports it, and the
sections below say what each check reports. The one exception is a scheduled check: a
schedule can't tick a box, so it needs a template of its own with `["--check"]` as its CLI
args.

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

5. **A task template "Proxmox upgrade"**, playbook `ansible/proxmox-upgrade.yml`, the
   inventory from step 3, the repository from step 2 and the "Kubernetes" variable group. A
   dry run runs the checks and lists the pending upgrades, changing nothing; it's safe to
   schedule (e.g. weekly, from its own template with `["--check"]`). A real run is started by
   hand.

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

4. **A task template "Kubernetes nodes: update"**, playbook `ansible/k8s-node-update.yml`,
   the "Kubernetes nodes" inventory, the repository and the "Kubernetes" variable group.

**Rebooting the nodes** (`k8s-node-reboot.yml`): one more template on the same inventory,
repository and variable group, **"Kubernetes nodes: reboot"**. To reboot only some nodes, set the template's (or the run's)
limit to those nodes plus `localhost`, e.g. `<node>,localhost`; without `localhost`
the run stops at its first check, having changed nothing.

**Reaching the nodes on their management addresses** from beyond the management network
(`tasks/k8s-node-mgmt.yml`, run by `k8s-node-update.yml` on each node before its upgrade; no
drain needed). The nodes' management interface has no gateway, so a reply from a management
address to anywhere off that network is sent out the main interface and dropped. Two
settings in the "Kubernetes" variable group, both off until set:

- `mgmt_gateway`: the management network's gateway. Each node gets routing table 250
  (the management subnet and a default route via that gateway) and a rule sending traffic
  from its management address there, re-added by `/etc/network/if-up.d/mgmt-return` whenever
  the interface comes up. Pods and the node's own connections are unaffected.
- `mgmt_ssh_only: true`: sshd listens on the management address only. It refuses to run
  without `mgmt_gateway`, or when the inventory reaches the node by another address. Set it
  only after a run with the gateway alone, and after SSH from your PC to a node's management
  address has worked. If a node is ever unreachable, `pct enter <id>` on its Proxmox host
  still works.

With both empty, a run still reports each node's routing and sshd listening addresses.
`mgmt_interface` (default `eth1`) and `mgmt_table` (default `250`) are there if they ever
differ.

What a run does to the workloads: each database cluster with two instances keeps running
(its primary is switched over before the drain, and its replica waits for the node); a
single-instance one (there are none now) would be down while its node updates. If a run stops
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
variables (`{"k8s_bundle": "1.33"}`, a string): **"Kubernetes upgrade"**. Change the bundle for
each step, and dry-run it first.

**A new bundle:** copy the newest, set the versions, and give every add-on's range from its
project's support page, with the link. A new add-on goes into `k8s_addons` with how to read
its version, and a range in every bundle. Once the cluster runs a new minor, bump `KUBECTL_VERSION`
in `docker/claude-code/Dockerfile` to match: kubectl supports one minor version either side of
the server.

## The Pi-holes and the Docker VM (`pihole-update.yml`, `pihole-config.yml`, `docker-vm-update.yml`)

`pihole-update.yml` and `docker-vm-update.yml` haven't run yet; dry-run each first.

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
- **Templates:** one for each, with the inventory and the repository; none needs the
  "Kubernetes" variable group.
- **The Pi-hole settings** for `pihole-config.yml` live in a variable group of their own
  ("Pi-hole settings"), as extra variables, since they name the site's domains:
  `{"pihole_settings": {"misc.dnsmasq_lines": [...], "webserver.api.app_sudo": true}}`. The
  playbook's header says which settings it may own. Run it after changing a value there, and
  on a schedule in check mode to report drift.

## Trusted certificates on the Proxmox hosts (`proxmox-host.yml`)

Off until `acme_domain` is set. A variable group "Proxmox ACME", extra variables, added to the
"Proxmox host" template:
`{"acme_domain": "<domain>", "acme_email": "<you>", "acme_cf_zone_id": "<zone ID>"}`, and as a
**secret** extra variable `acme_cf_token`: a Cloudflare API token of its own with **Zone → DNS
→ Edit** on that zone only (Cloudflare: My Profile → API Tokens → Create Token, the "Edit zone
DNS" template). The zone ID is on the zone's Overview page.

Each host's name is `<its node name>.<acme_domain>` (`acme_fqdn` per host overrides it). The
names are published in Certificate Transparency logs, like every public certificate's. The run
also writes a record for each name, pointing at the host's inventory address (`ansible_host`,
an IPv4 address), into the primary Pi-hole's local records: through `pct exec` on the host
carrying that container, with Pi-hole's own CLI, so it needs no Pi-hole credential. The primary
is the container tagged `dns_guest_tag` with the lowest VMID (the inventory already sets the
tag for `proxmox-upgrade.yml`), or `pihole_primary_vmid`; nebula-sync copies the records to
the other Pi-hole. A run limited to hosts that don't carry it stops before changing anything.
Running the playbook registers the Let's Encrypt account, which accepts its Subscriber
Agreement. Proxmox renews the certificates itself.

## NAS block storage on the Proxmox hosts (`proxmox-host.yml`)

Each host gets its own iSCSI LUN on the NAS as storage `nas-block`: one entry, LVM, not shared,
on every host, the way `local-lvm` is each host's own disk. A container moved to another host
has its `nas-block` volumes copied to that host's LUN. It holds data that writes constantly
(Prometheus), so those writes land on the NAS's drives, not the hosts' SSDs.

1. **On the NAS** (SAN Manager): one target that allows multiple sessions, bound to the
   management connection; one thin LUN per host, each read/write for that host's initiator only
   (`/etc/iscsi/initiatorname.iscsi` on the host), OS type Linux.
2. **Variables** in the Proxmox hosts' variable group: `nas_block_portal` (the NAS's
   management address) and `nas_block_target` (the target's IQN). Run "Proxmox: host
   settings" as a dry run: each host should report one LUN, blank.
3. **`nas_block_initialise: true`** and a real run: the volume group goes on each blank LUN, then
   the storage entry is defined once all hosts have theirs. A LUN holding anything else stops
   the run whatever the setting. Afterwards `nas_block_initialise` can go again.

## Your own login on every machine (`admin-users.yml`)

- **A variable group "Admin user"**, extra variables:
  `{"admin_user": "dhardin", "admin_ssh_keys": ["ssh-ed25519 AAAA... comment"], "admin_sudo_nopasswd": true}`.
  The public key line is the one `ssh-add -L` shows for the key in your vault. For sudo with a
  password instead, set `admin_sudo_nopasswd` to false and add `admin_password_hash` (from
  `openssl passwd -6`) as a secret.
- **Run it through `baseline.yml`** (below), which takes the same variable group; a template
  of `admin-users.yml` alone is only for the admin login without the rest.
- **Afterwards**, `ssh dhardin@<host>` and `sudo -n true` prove it on each machine; then
  `~/.ssh/config` can say `User dhardin`.

## Every Debian host at once (`baseline.yml`)

- **A shared SSH key for Semaphore.** On the Docker host, `ssh-keygen -t ed25519 -N '' -C
  semaphore-shared -f semaphore-shared`; the private half goes into the Key Store as
  "Shared", the `.pub` line into the "Admin user" variable group as `semaphore_ssh_key`, and
  then delete both files.
- **A "Baseline" template per existing inventory** (Proxmox, Kubernetes nodes, Pi-holes,
  Docker VM), playbook `ansible/baseline.yml`, the "Admin user" variable group. Run each
  once (a dry run first): that puts the shared key on every host through the keys that
  already work.
- **The inventory "All Debian Hosts"**, Static YAML with the "Shared" key: every host, in
  the groups the other playbooks use, so a playbook pointed at it still touches only its own
  group:

  ```yaml
  proxmox:
    hosts:
      <host>: {ansible_host: <management address>}
  k8s_nodes:
    children:
      k8s_control_planes:
        hosts: {<node>: {ansible_host: <management address>}}
      k8s_workers:
        hosts: {<node>: {ansible_host: <management address>}}
  pihole:
    hosts: {<pi-hole>: {ansible_host: <management address>}}
  docker_vm:
    hosts: {<docker vm>: {ansible_host: <management address>}}
  all:
    vars:
      ansible_user: root
      ansible_python_interpreter: /usr/bin/python3
  ```

  Keep each group's own variables (for example the nodes' `kubeadm_ignore_preflight_errors`)
  under that group. The host keys are the ones already pinned in Semaphore's `known_hosts`.
- **One template "All Debian hosts: baseline"** on that inventory replaces the per-inventory
  ones, and `admin-users.yml`'s templates. The update and upgrade playbooks keep their own
  narrower inventories, so a mis-set limit can't reach the wrong machines.

The bash setup is loaded before each user's `~/.bashrc`, so anything set there wins: Debian's
default `~/.bashrc` for a new account sets its own prompt, and the baseline's coloured prompt
shows only where that line is removed (root's default `~/.bashrc` doesn't set one).

## Running a Proxmox upgrade

1. Dry-run **"Proxmox upgrade"** and read the pending upgrades per host.
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
