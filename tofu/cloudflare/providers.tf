# The token comes from CLOUDFLARE_API_TOKEN in Semaphore's environment: its own
# token, Zone > DNS > Edit and Zone > Zone > Read on these zones only.
provider "cloudflare" {}
