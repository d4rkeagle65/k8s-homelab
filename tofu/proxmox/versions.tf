terraform {
  # The OpenTofu in Semaphore's image (docker/semaphore), and in claude-code's
  # for fmt and validate.
  required_version = "1.11.0"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "0.116.0"
    }
  }

  # State in Semaphore's PostgreSQL (database tfstate, tfstate-db-init in
  # docker/semaphore/compose.yaml), one schema per root module. The connection
  # string comes from PG_CONN_STR in Semaphore's environment.
  backend "pg" {
    schema_name = "proxmox"
  }
}
