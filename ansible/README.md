# Ansible playbooks

Run from Semaphore on the Docker host (`docker/semaphore/`), which pulls this folder from git.
What's specific to one site lives only in Semaphore, never here: the inventory (host names
and addresses), the SSH key, and the Kubernetes token.

| Playbook | What it does |
|---|---|
| `proxmox-upgrade.yml` | Upgrades the Proxmox hosts one at a time (`apt dist-upgrade`, `autoremove`), rebooting only when a new kernel or Debian asks for it. Before each host and after any reboot it checks the Proxmox cluster has quorum and every Kubernetes node is Ready, and stops the run if not. Hosts in `no_reboot` are upgraded but left for you to reboot. |

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

3. **The inventory.** **Inventory** > New, type Static, user credentials the key from step 1,
   with the hosts in the order to upgrade them and the one carrying the Docker VM last and in
   `no_reboot`:

   ```ini
   [proxmox]
   <host 1> ansible_host=<management address>
   <host 2> ansible_host=<management address>
   <host 3> ansible_host=<management address>
   <docker VM's host> ansible_host=<management address>

   [no_reboot]
   <docker VM's host>

   [proxmox:vars]
   ansible_user=root
   ```

4. **The Kubernetes access.** Semaphore reads the cluster through the read-only
   `cluster-access/semaphore` ServiceAccount (`kubernetes/prod/system/cluster-access/`).
   **Variable Groups** > New, "Kubernetes", with:
   - `k8s_api_url`: the API server URL, the same as in your kubeconfig.
   - `k8s_ca_pem` (a secret): the cluster's CA certificate. On the PC:

     ```powershell
     [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String((kubectl -n cluster-access get secret semaphore-token -o jsonpath='{.data.ca\.crt}'))) | Set-Clipboard
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

## Running an upgrade

1. Run **"Proxmox upgrade: check"** and read the pending upgrades per host.
2. Run **"Proxmox upgrade"**. It stops at the first failed check, before touching the next
   host; fix the cause and run it again (upgraded hosts have nothing left to do).
3. When it reports a host that still needs a reboot by hand (the Docker VM's), reboot that host
   from Proxmox when convenient. Pi-hole is on it too, so DNS is down while it restarts.

## Checks for a playbook here

- `ansible-playbook --syntax-check` passes, with any inventory.
- Anything a playbook reads from a cluster or a host it reads with `changed_when: false` and
  `check_mode: false`, so a check-mode run reports real facts.
- A token or password goes in Semaphore, never in this repo, and a task that sends one carries
  `no_log: true`.
