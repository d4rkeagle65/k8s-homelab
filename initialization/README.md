# k8s-homelab Cluster Init

Within this folder are my notes/commands for initializing my k8s homelab cluster. This is very much a rough draft and will eventually be refactored into Terraform/Ansible configs.

You will need to generate and copy a ssh-key to the remote PVE host. (Note: At this time the scripts only work if pve_host_username is root. I will be fixing this at a later date)

```shell
ssh-keygen -t ed25519 -C "local_username@local_workstation"
ssh-copy-id pve_host_username@remote_pve_host
```

You will also need a file located in the k8s-homelab/secrets folder named pve-lxc-setup.ini with the values:

```shell
LXC_ROOT_PASS=""
WORKSTATION_SSHKEY='ssh-ed25519 ... local_username@local_workstation'
```