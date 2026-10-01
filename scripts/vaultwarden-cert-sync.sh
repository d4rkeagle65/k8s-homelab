#!/bin/sh
# Runs on the Docker host that runs Vaultwarden, not in the cluster. Copies
# the vaultwarden-tls certificate (issued by cert-manager, see
# kubernetes/apps/external-services/external-services/app/vaultwarden.yaml)
# to this host, and restarts Vaultwarden when it changed: Vaultwarden only
# reads its certificate at startup. Run it daily from cron; it does nothing
# while the certificate is unchanged.
#
# Needs curl and jq. Reads its settings from $CONF (default
# /etc/vaultwarden-cert-sync), all root-only:
#   server   the Kubernetes API URL, e.g. https://<control plane>:6443
#   token    the vaultwarden-cert-sync ServiceAccount token
#   ca.crt   the cluster CA, to check the API server's certificate
# Both of the last two come from the vaultwarden-cert-sync-token Secret.
#
# Environment overrides: DEST (where Vaultwarden reads tls.crt/tls.key,
# default /opt/vaultwarden/ssl), CONTAINER (default vaultwarden).
set -eu

CONF=${CONF:-/etc/vaultwarden-cert-sync}
DEST=${DEST:-/opt/vaultwarden/ssl}
CONTAINER=${CONTAINER:-vaultwarden}
URL="$(cat "$CONF/server")/api/v1/namespaces/external-services/secrets/vaultwarden-tls"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

curl -fsS --cacert "$CONF/ca.crt" \
    -H "Authorization: Bearer $(cat "$CONF/token")" "$URL" > "$tmp/secret.json"
jq -r '.data["tls.crt"]' "$tmp/secret.json" | base64 -d > "$tmp/tls.crt"
jq -r '.data["tls.key"]' "$tmp/secret.json" | base64 -d > "$tmp/tls.key"

# Never install something that isn't a certificate and key.
grep -q "BEGIN CERTIFICATE" "$tmp/tls.crt"
grep -q "BEGIN PRIVATE KEY" "$tmp/tls.key"

if cmp -s "$tmp/tls.crt" "$DEST/tls.crt" 2>/dev/null &&
   cmp -s "$tmp/tls.key" "$DEST/tls.key" 2>/dev/null; then
    exit 0
fi

install -d -m 700 "$DEST"
install -m 644 "$tmp/tls.crt" "$DEST/tls.crt"
install -m 600 "$tmp/tls.key" "$DEST/tls.key"
echo "vaultwarden-cert-sync: installed a new certificate in $DEST"

# The first run happens before Vaultwarden uses TLS; don't fail on a
# container that isn't there yet.
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
    docker restart "$CONTAINER" >/dev/null
    echo "vaultwarden-cert-sync: restarted $CONTAINER"
fi
