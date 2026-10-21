# Waits, before anything here changes, until none of this module's guests holds
# a Proxmox lock: the nightly backup locks each guest while it copies it, and a
# change to a locked guest fails, leaving the apply half done. A data source is
# read at every plan and again as the apply Semaphore runs after its confirmation
# starts, so the wait comes just before the changes and adds none to the plan.
# A plan run during a backup waits for it too, up to the timeout, then fails
# without changing anything (wait-for-unlocked-guests.sh).
data "external" "guests_unlocked" {
  program = ["sh", "${path.module}/wait-for-unlocked-guests.sh"]

  query = {
    vmids            = join(",", sort(tolist(local.guests_needing_netbox)))
    timeout_seconds  = "14400"
    interval_seconds = "30"
  }
}
