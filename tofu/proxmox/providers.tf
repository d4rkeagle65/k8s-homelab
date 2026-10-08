# Endpoint and token come from the environment Semaphore runs it in:
# PROXMOX_VE_ENDPOINT (https://<host>:8006/), PROXMOX_VE_API_TOKEN
# (user@realm!tokenid=secret) and PROXMOX_VE_INSECURE (true: the hosts'
# certificates are self-signed).
provider "proxmox" {}
