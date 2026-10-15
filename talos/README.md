# Talos nodes

The cluster's nodes are Talos Linux VMs, built by OpenTofu (`tofu/proxmox/talos.tf`) and
upgraded by `ansible/talos-upgrade.yml` and `ansible/talos-k8s-upgrade.yml`. This folder
holds what isn't secret:

- `patches/`: the Talos settings every node gets. `common.yaml` names the installer image
  (Talos version and Image Factory schematic); `encryption-order.txt` is the order of the
  control planes' encryption providers for Secrets at rest, and says why it changes in stages;
  the `controlplane-podsecurity*` pair sets Pod Security admission (privileged enforced, as
  under kubeadm); `worker-local-db.yaml` makes a worker's second disk its local-db volume.
- `gen-config.py`: builds the machine configs from the owner's `secrets.yaml`, the patches,
  and what it reads from the live cluster (API name, DNS domain, subnets, the nodes'
  network), so nothing internal is kept here. It checks before it writes: `talosctl`'s
  version against the image's, the API servers' first service-account issuer against the
  API address, and every output with `talosctl validate`.

The secrets (`talosctl gen secrets --from-kubernetes-pki`, so the cluster's own CAs) are
`TALOS_SECRETS` in the Vaultwarden item `cluster-secrets` reads, base64 on one line; git
maps it nowhere. The generated configs hold the CA keys too: make them, apply them, delete
them.

## A node by hand

Commands with `<...>` take the node's own values (in chat, never here).

**1. The VM, on its Proxmox host.** The `nocloud` disk image of the schematic in
`patches/common.yaml` (`https://factory.talos.dev/image/<schematic>/<version>/nocloud-amd64.raw.xz`),
unpacked with `xz -d`, then:

```bash
qm create <id> --name <node name> --memory 4096 --cores 2 --cpu x86-64-v2-AES \
  --scsihw virtio-scsi-single --net0 virtio,bridge=vmbr7 \
  --agent enabled=1 --ostype l26 --onboot 1
qm disk import <id> /var/tmp/nocloud-amd64.raw vms
qm set <id> --scsi0 vms:vm-<id>-disk-0,discard=on,ssd=1,iothread=1 --boot order=scsi0
qm disk resize <id> scsi0 32G
qm set <id> --ide2 vms:cloudinit --ipconfig0 ip=<address>/24,gw=<gateway> --nameserver <pi-hole>
qm start <id>
```

It boots into maintenance mode with the address from cloud-init and its name as hostname
(the Kubernetes node's name). Check from the workstation:
`talosctl -n <address> get disks --insecure` lists `sda`.

**2. The configs, on the workstation** (PowerShell, `talosctl` of the image's version on
PATH, `kubectl` on the cluster). Copy `TALOS_SECRETS` from Vaultwarden to the clipboard;
it's too long to paste at a prompt.

```powershell
$dir = "$env:USERPROFILE\talos-work"; New-Item -ItemType Directory -Force $dir | Out-Null
[IO.File]::WriteAllBytes("$dir\secrets.yaml", [Convert]::FromBase64String((Get-Clipboard -Raw).Trim()))
Set-Clipboard -Value " "
py -3 talos\gen-config.py --secrets "$dir\secrets.yaml"
```

**3. Before a control plane joins:** etcd healthy on every member
(`talosctl etcd members`, `talosctl etcd status`) and a snapshot taken
(`talosctl etcd snapshot <file>`).

**4. Apply, then watch it join:**

```powershell
talosctl apply-config --insecure -n <address> --file "$dir\controlplane.yaml"   # worker.yaml for a worker
talosctl --talosconfig "$dir\talosconfig" config endpoint <address>
talosctl --talosconfig "$dir\talosconfig" config node <address>
talosctl --talosconfig "$dir\talosconfig" dmesg --follow     # Ctrl+C once it's up
talosctl --talosconfig "$dir\talosconfig" etcd members       # control planes: one more member
kubectl get node <node name> -o wide                         # Ready, its address on the main network
```

**5. Delete them.** `talosconfig` is the admin credential for the Talos API, signed by a CA
in the secrets, so `gen-config.py` makes a working one again whenever it's needed; keep it
only while working on the node. Then:
`Remove-Item "$dir\secrets.yaml","$dir\controlplane.yaml","$dir\worker.yaml","$dir\talosconfig","$dir\site-patch*.yaml"`.
