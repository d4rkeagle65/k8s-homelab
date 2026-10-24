terraform {
  # The OpenTofu in Semaphore's image (docker/semaphore), and in claude-code's
  # for fmt and validate.
  required_version = "1.11.0"
  required_providers {
    tailscale = {
      source  = "tailscale/tailscale"
      version = "0.29.2"
    }
    http = {
      source  = "hashicorp/http"
      version = "3.6.2"
    }
  }
  # State in Semaphore's PostgreSQL, like proxmox/ and cloudflare/, in a schema of its own.
  backend "pg" {
    schema_name = "tailscale"
  }
}
