terraform {
  # The OpenTofu in Semaphore's image (docker/semaphore), and in claude-code's
  # for fmt and validate.
  required_version = "1.11.0"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "0.117.0"
    }
    # The Talos nodes' configs and their applying (talos-config.tf, talos.tf).
    talos = {
      source  = "siderolabs/talos"
      version = "0.12.0"
    }
    # The Talos admin client certificate (talos-config.tf).
    tls = {
      source  = "hashicorp/tls"
      version = "4.4.1"
    }
    # Each guest's host, name and addresses, from NetBox's GraphQL (netbox.tf).
    http = {
      source  = "hashicorp/http"
      version = "3.6.2"
    }
  }

  # State in Semaphore's PostgreSQL (database tfstate, tfstate-db-init in
  # docker/semaphore/compose.yaml), one schema per root module. The connection
  # string comes from PG_CONN_STR in Semaphore's environment.
  backend "pg" {
    schema_name = "proxmox"
  }
}
