# The OAuth client from TAILSCALE_OAUTH_CLIENT_ID and TAILSCALE_OAUTH_CLIENT_SECRET in
# Semaphore's environment (Key Vault's TAILSCALE-OPENTOFU-OAUTH-CLIENT-*): write on the
# policy file, DNS, devices and feature settings. The tailnet is the client's own.
provider "tailscale" {}
