# Endpoint and token come from the environment Semaphore runs it in:
# PROXMOX_VE_ENDPOINT (https://<host name>:8006/, the name on the host's
# Let's Encrypt certificate, which is verified) and PROXMOX_VE_API_TOKEN
# (user@realm!tokenid=secret).
provider "proxmox" {}
