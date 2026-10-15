# Ansible playbooks

Run from Semaphore on the Docker host (`docker/semaphore/`), which pulls this folder from git.
What's specific to one site lives only in Semaphore, never here: the inventory (host names
and addresses), the SSH key, and the Kubernetes token.

| Playbook | What it does |
|---|---|
| `proxmox-upgrade.yml` | Upgrades the Proxmox hosts one at a time (`apt dist-upgrade`, `autoremove`), rebooting only when a new kernel or Debian asks for it. Before each host and after any reboot it checks the Proxmox cluster has quorum and every Kubernetes node is Ready, and stops the run if not. A reboot first drains the Kubernetes nodes running on the host and afterwards waits for every database cluster to be whole (`tasks/proxmox-host-reboot.yml`). It live-migrates the VMs listed in `evacuate` off a host before rebooting it and back afterwards, and, with two Pi-holes or more, never lets DNS go fully down: a host carrying one reboots only while the others answer. Hosts in `no_reboot` are upgraded but left for you to reboot. |
| `proxmox-reboot.yml` | Reboots Proxmox hosts one at a time, changing nothing else: `reboot_hosts` picks them (one host, a comma-separated list or a group; `ALL` or empty, every host, one at a time). For each: the Kubernetes nodes running on it are drained (each database primary switched to a replica elsewhere first; two control planes on one host stops the run, as it would cost etcd its quorum), its Proxmox HA VMs moved off by putting it in HA maintenance mode (HA's node-affinity rules refuse a manual move while their preferred node is up; the run first checks some node has room for each, and stops if not) and the VMs in `evacuate` live-migrated off, then the reboot, maintenance off (HA moves its VMs back where this host is their preferred node), and the run waits for quorum, every node Ready, the host's nodes uncordoned, every database cluster whole and the VMs moved back before the next host. The same steps as `proxmox-upgrade.yml`'s reboots; the same inventory and variables. A dry run lists what each host's reboot would drain, move and restart. |
| `talos-upgrade.yml` | Upgrades Talos on the Kubernetes nodes, one at a time, control planes first, skipping nodes already on the target (the installer image in `talos/patches/common.yaml`). Each node: health and capacity checks, drained (database primaries switched over first), `talosctl upgrade`, back Ready on the new version, uncordoned, every database cluster whole. Change the image in a commit, run this, then the OpenTofu job. `talos_nodes_only` limits it to some nodes. A dry run lists each node's version and what it would upgrade. |
| `talos-k8s-upgrade.yml` | Upgrades Kubernetes with `talosctl upgrade-k8s` to `kubernetes_version` in `tofu/proxmox/talos-config.tf`, with the cluster and every database checked healthy before and after. Change the version in a commit, run this, then the OpenTofu job (the other way round, OpenTofu would push it to every control plane at once). A dry run runs `upgrade-k8s --dry-run`. After a new minor, bump `KUBECTL_VERSION` in `docker/claude-code/Dockerfile` to match: kubectl supports one minor either side of the server. |
| `proxmox-host.yml` | The Proxmox hosts' own settings, on every host at once (no reboots, no guests touched): the ZFS ARC cap (`zfs_arc_max_gib`, default 10% of RAM, at least 2, into `/etc/modprobe.d/zfs.conf`, the initramfs when root is on ZFS, and the running module, read back from the ARC's statistics), and a trusted certificate for each host's web UI and API (`tasks/proxmox-acme.yml`: Let's Encrypt through Proxmox's ACME client and Cloudflare DNS, ordered when missing or within 30 days of expiry, then read back). Each host's own iSCSI LUN on the NAS as storage `nas-block` (`tasks/proxmox-nas-block.yml`, off until set). TSO, GSO and GRO off on every Intel `e1000e` NIC, now and at boot through a udev rule, as that driver hangs under load (`tasks/proxmox-nic-offloads.yml`). Intel AMT Serial-over-LAN where a host has it (`tasks/proxmox-amt-sol.yml`, below). A dry run reports each host's ARC, certificate, NAS block storage, NIC offloads and SOL setup. |
| `pihole-update.yml` | Updates the Pi-holes one at a time (`apt dist-upgrade`, then `pihole -up`), each only while every other one answers DNS, and waits for it to answer again before the next. With a single Pi-hole it stops unless `allow_dns_outage: true`. |
| `pihole-config.yml` | Sets every Pi-hole's `pihole-FTL` settings to the values in `pihole_settings` (a Semaphore variable group), one at a time and only while the others answer DNS: sets each that differs, reads it back, restarts FTL and waits for it to answer again. It refuses settings nebula-sync copies from Pi-hole 1, so each setting has one owner. Check mode reports the differences. |
| `baseline.yml` | The same baseline on every Debian host at once, from the "All Debian Hosts" inventory: the admin login (`admin-users.yml`'s steps), Semaphore's shared key for root, the packages in `vars/baseline.yml` (installed when missing, never removed or upgraded), and one interactive bash setup (`files/homelab-bashrc`). Check mode reports each part. |
| `admin-users.yml` | The admin login alone, on every host of the inventory it runs against (`baseline.yml` runs the same steps, `tasks/admin-user.yml`): the account `admin_user` with exactly `admin_ssh_keys` in its `authorized_keys`, `sudo` installed, and sudo through Debian's `sudo` group (passwordless with `admin_sudo_nopasswd: true`). It checks the result the way sudo sees it (`sudo -l -U`), so a group sudoers doesn't grant fails the run. Root's logins and `sshd` are left alone. |
| `docker-vm-update.yml` | Updates the Docker VM's packages with Docker held, and reports when a reboot or a Docker upgrade is due; it never does either, since Semaphore runs there. Not yet run. |
| `openwrt-aps.yml` | Prometheus's exporter on the OpenWrt access points (OpenWrt's `prometheus-node-exporter-lua` with its Wi-Fi collectors), listening on one interface's address, then read back from there. Only `raw`, since OpenWrt has no Python. Check mode reports the missing packages and the listening interface. |

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

4. **The Kubernetes access.** Semaphore reaches the cluster through the
   `cluster-access/semaphore` ServiceAccount (`kubernetes/prod/system/cluster-access/`):
   it reads the nodes and may cordon them, evict pods and switch database primaries over,
   for the reboots; it reads no Secrets. The playbooks that drain download a kubectl
   matching the cluster into a private temporary directory and remove it afterwards.
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

   **The Talos access**, for `talos-upgrade.yml` and `talos-k8s-upgrade.yml`: `talosconfig_b64`,
   an `os:admin` talosconfig (Talos lets only `os:admin` upgrade or apply configs), base64 on one
   line, as a secret in the same variable group. `talos/gen-config.py` writes one beside the
   configs (`talos/README.md`); on the PC, from that folder:

   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("$dir\talosconfig")) | Set-Clipboard
   ```

   Keep a copy in Vaultwarden, clear the clipboard, and delete the file as `talos/README.md` says.

5. **A task template "Proxmox upgrade"**, playbook `ansible/proxmox-upgrade.yml`, the
   inventory from step 3, the repository from step 2 and the "Kubernetes" variable group. A
   dry run runs the checks and lists the pending upgrades, changing nothing; it's safe to
   schedule (e.g. weekly, from its own template with `["--check"]`). A real run is started by
   hand.

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

## Serial-over-LAN on the Proxmox hosts (`proxmox-host.yml`)

`tasks/proxmox-amt-sol.yml` sets up the Linux side of Intel AMT's Serial-over-LAN on every
host whose AMT shows a serial port (an Intel 16550 at 00:16.3, "KT Controller" or "Active
Management Technology - SOL" in lspci); a host without one is reported and
left alone. AMT itself (provisioning, SOL enabled in MEBx or MeshCommander) is set by hand, and
SOL only works over the NIC AMT is bound to, the onboard Intel one.

- **What it sets:** `console=tty0 console=ttyS<N>,115200n8` on the kernel command line (the
  serial one last, so it's `/dev/console`), GRUB's menu on the same port where GRUB boots the
  host, and `serial-getty@ttyS<N>` enabled and started. The tty and its I/O port are read per
  host; `amt_sol_tty` and `amt_sol_port` override them.
- **Where the command line lives:** `/etc/default/grub` (`update-grub`) for GRUB on its own,
  the same file with `proxmox-boot-tool refresh` for GRUB on proxmox-boot-tool's ESPs, and
  `/etc/kernel/cmdline` for systemd-boot on them (which has no serial menu). A host it can't
  place stops with proxmox-boot-tool's output.
- **Reboots:** a boot change sets `/run/reboot-required`, so the next `proxmox-upgrade.yml`
  run reboots that host the usual careful way. Nothing here reboots a host.
- **No tty:** if the KT Controller has no tty, 8250 ran out of UARTs; the run adds
  `8250.nr_uarts=8`, and the console and getty come with the run after the reboot.
- **Settings:** `amt_sol` (default true; false reports only), `amt_sol_baud` (115200),
  `amt_sol_keep_vga` (true), `amt_sol_grub_menu` (true), `amt_sol_nr_uarts` (8).
- **Check after the reboot:** `cat /proc/cmdline` shows the two consoles, `systemctl status
  serial-getty@ttyS<N>` is active, and MeshCommander's Terminal (or `amtterm`) shows a login
  prompt, and GRUB's menu on the next boot.

## NAS block storage on the Proxmox hosts (`proxmox-host.yml`)

Each host gets its own iSCSI LUN on the NAS as storage `nas-block`: one entry, LVM, not shared,
on every host, the way `local-lvm` is each host's own disk. A container moved to another host
has its `nas-block` volumes copied to that host's LUN. It holds data that writes constantly
(Prometheus), so those writes land on the NAS's drives, not the hosts' SSDs.

1. **On the NAS** (SAN Manager): one target that allows multiple sessions, bound to the
   management connection **only** (bound to both, it is advertised on both and each host logs in
   twice, so its LUN appears as two disks; the run stops on that); one thin LUN per host, each read/write for that host's initiator only
   (`/etc/iscsi/initiatorname.iscsi` on the host), OS type Linux.
2. **Variables** in the Proxmox hosts' variable group: `nas_block_portal` (the NAS's
   management address) and `nas_block_target` (the target's IQN). Run "Proxmox: host
   settings" as a dry run: each host should report one LUN, blank.
3. **`nas_block_initialise: true`** and a real run: the volume group goes on each blank LUN, then
   the storage entry is defined once all hosts have theirs. A LUN holding anything else stops
   the run whatever the setting. Afterwards `nas_block_initialise` can go again.

A host without a LUN of its own (one that only carries Talos VMs, which don't use `nas-block`)
gets `nas_block: false` as a host variable in the inventory: the run leaves it out, and the
storage entry lists only the other hosts.

## Your own login on every machine (`admin-users.yml`)

- **A variable group "Debian Host Baseline"**, extra variables:
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
  "Shared", the `.pub` line into the "Debian Host Baseline" variable group as `semaphore_ssh_key`, and
  then delete both files.
- **A "Baseline" template per existing inventory** (Proxmox, Pi-holes, Docker VM), playbook `ansible/baseline.yml`, the "Debian Host Baseline" variable group. Run each
  once (a dry run first): that puts the shared key on every host through the keys that
  already work.
- **The inventory "All Debian Hosts"**, Static YAML with the "Shared" key: every host, in
  the groups the other playbooks use, so a playbook pointed at it still touches only its own
  group:

  ```yaml
  proxmox:
    hosts:
      <host>: {ansible_host: <management address>}
  pihole:
    hosts: {<pi-hole>: {ansible_host: <management address>}}
  docker_vm:
    hosts: {<docker vm>: {ansible_host: <management address>}}
  all:
    vars:
      ansible_user: root
      ansible_python_interpreter: /usr/bin/python3
  ```

  Keep each group's own variables under that group. The host keys are the ones already pinned in Semaphore's `known_hosts`.
- **One template "All Debian hosts: baseline"** on that inventory replaces the per-inventory
  ones, and `admin-users.yml`'s templates. The update and upgrade playbooks keep their own
  narrower inventories, so a mis-set limit can't reach the wrong machines.

**Prometheus's exporters** (`tasks/host-exporters.yml`), off until `host_exporters: true` is in
the variable group: Debian's `prometheus-node-exporter` on every host here (the Kubernetes
nodes, Talos, run the cluster's own), and on the Proxmox hosts the packaged SMART and NVMe
collectors (each SSD's wear) on their timers. Each listens only on the host's own address in
`management_network` (the management network with its prefix, set once in the variable group),
found on the host itself, so no host needs an address set; a host with none or several there
stops the run. Prometheus finds
the Proxmox hosts through `proxmox-hosts.<domain>`, a Pi-hole name `proxmox-host.yml` writes
with one address per host, and the Docker VM by its management name.

The bash setup is loaded before each user's `~/.bashrc`, so anything set there wins: Debian's
default `~/.bashrc` for a new account sets its own prompt, and the baseline's coloured prompt
shows only where that line is removed (root's default `~/.bashrc` doesn't set one).

## The OpenWrt access points (`openwrt-aps.yml`)

- **Key.** OpenWrt's SSH server (dropbear) reads root's keys from
  `/etc/dropbear/authorized_keys`, not `/root/.ssh`: add Semaphore's shared key there on each
  access point (LuCI: System > Administration > SSH-Keys).
- **Host keys.** Pin them on the Docker host as for the others
  (`docker/semaphore/README.md`).
- **Inventory** "OpenWrt APs", Static YAML, Semaphore's shared key, an `openwrt_aps` group,
  user `root`, each access point by the address Prometheus scrapes, the same one as its
  `OPENWRT_AP<n>_ADDRESS` variable (`ap1` to `ap3`, in that order, are the cards Access point 1
  to 3). Set `openwrt_exporter_interface` when that address isn't on OpenWrt's `lan`
  interface.
- **An access point behind another tailnet subnet router** (a travel router advertising its
  own subnet): the Docker VM reaches it through Tailscale from its own tailnet address,
  carrying its tag, while the cluster's traffic keeps its LAN address. It needs a grant in
  the Tailscale policy from that tag to the subnet on TCP 22 and 9100, and a rule on the
  router accepting both sources on those ports from the zone the traffic arrives in.
- **Template** with the inventory and the repository; nothing else. The access points need
  their package feeds (the internet) for the first run.

## Running a Proxmox upgrade

1. Dry-run **"Proxmox upgrade"** and read the pending upgrades per host.
2. Run **"Proxmox upgrade"**. It stops at the first failed check, before touching the next
   host; fix the cause and run it again (upgraded hosts have nothing left to do).
3. When it reports a host that still needs a reboot by hand, reboot that host from Proxmox when
   convenient, after moving anything that has to stay up.

## Checks for a playbook here

- It starts with `import_playbook: require-hosts.yml` for the group it works on. An inventory
  Ansible can't parse (a tab in Semaphore's YAML) only draws warnings, then every play matches
  nothing and the run exits 0: Semaphore shows a success that did nothing.
- `ansible-playbook --syntax-check` passes, with any inventory.
- In a block scalar (`>-`, `|`), a regex takes a single backslash: `'(\d+)'`, `'\1'`. YAML
  keeps a block's backslashes as they are, so a doubled one reaches the regex as a literal
  backslash and never matches, quietly (a `search` is just false) or with a crash (a
  `regex_search` backreference). Inside double quotes it's the other way: YAML halves them.
- Anything a playbook reads from a cluster or a host it reads with `changed_when: false` and
  `check_mode: false`, so a check-mode run reports real facts.
- A token or password goes in Semaphore, never in this repo, and a task that sends one carries
  `no_log: true`.
