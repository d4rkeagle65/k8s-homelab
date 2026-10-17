terraform {
  # The OpenTofu in Semaphore's image (docker/semaphore), and in claude-code's
  # for fmt and validate.
  required_version = "1.11.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "5.28.0"
    }
  }

  # State in Semaphore's PostgreSQL, like proxmox/, in a schema of its own.
  backend "pg" {
    schema_name = "cloudflare"
  }
}
