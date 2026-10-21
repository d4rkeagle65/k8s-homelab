#!/bin/sh
# Waits until none of this module's guests holds a Proxmox lock (a backup, a
# migration, a snapshot): a change to a locked guest fails ("VM is locked
# (backup)") and leaves the apply half done. Run by guests-unlocked.tf's external
# data source, so at every plan and again as the apply that follows starts.
#
# In (the data source's query, JSON on stdin): vmids, the guests' VMIDs separated
# by commas; timeout_seconds; interval_seconds. Out (JSON on stdout):
# waited_seconds. Proxmox's address and token are the provider's own
# environment, PROXMOX_VE_ENDPOINT and PROXMOX_VE_API_TOKEN. A Proxmox that
# doesn't answer, or a lock still held at the timeout, fails the run (stderr
# says which guests and why) rather than letting it go on.
set -eu

query=$(cat)
vmids=$(printf '%s' "$query" | jq -r '.vmids')
timeout=$(printf '%s' "$query" | jq -r '.timeout_seconds')
interval=$(printf '%s' "$query" | jq -r '.interval_seconds')
endpoint=${PROXMOX_VE_ENDPOINT%/}
start=$(date +%s)

while :; do
  # One listing has every guest and, for one that's locked, its lock.
  resources=$(curl -fsS --max-time 30 \
    -H "Authorization: PVEAPIToken=${PROXMOX_VE_API_TOKEN}" \
    "${endpoint}/api2/json/cluster/resources?type=vm") || {
    echo "Proxmox didn't answer ${endpoint}/api2/json/cluster/resources, so whether a guest is locked isn't known" >&2
    exit 1
  }
  locked=$(printf '%s' "$resources" | jq -r --arg ids "$vmids" '
    ($ids | split(",")) as $want
    | [.data[] | select((.vmid | tostring) as $v | $want | index($v) != null)
               | select((.lock // "") != "") | "\(.vmid) (\(.lock))"]
    | join(", ")')
  waited=$(($(date +%s) - start))
  [ -z "$locked" ] && break
  if [ "$waited" -ge "$timeout" ]; then
    echo "still locked after ${waited}s: ${locked}. Nothing was changed; run it again once they're free." >&2
    exit 1
  fi
  sleep "$interval"
done

jq -n --arg waited "$waited" '{waited_seconds: $waited}'
