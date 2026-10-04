# ISC's Kea image plus ISC's Stork agent and server and kea-admin, all from
# ISC's Cloudsmith repositories. One image for the stack's helpers: the Stork
# agent needs Kea's own binary beside it (it runs `kea-dhcp4 -v` to learn the
# version), and kea-admin creates and upgrades the reservations database.
# Keep the Kea version in step with the kea-dhcp4 service's image.
#
# Stork comes from ISC's development series (stork-dev): login through
# OIDC (Authentik) arrived in 2.5, and there's no stable 2.6 yet. Stork
# migrates its own database on start, one way; switch back to the stable
# repository (stork) once 2.6 is out.
FROM docker.cloudsmith.io/isc/docker/kea-dhcp4:3.2.1

ARG STORK_VERSION=2.5.1
ARG KEA_VERSION=3.2.1

SHELL ["/bin/ash", "-o", "pipefail", "-c"]
# kea-admin is a bash script and needs psql, so bash and the PostgreSQL
# client stay installed.
RUN apk add --no-cache curl bash && \
    curl -1sLf https://dl.cloudsmith.io/public/isc/stork-dev/setup.alpine.sh | bash && \
    curl -1sLf https://dl.cloudsmith.io/public/isc/kea-3-2/setup.alpine.sh | bash && \
    apk add --no-cache \
        "isc-stork-agent~=${STORK_VERSION}" \
        "isc-stork-server~=${STORK_VERSION}" \
        "isc-kea-admin~=${KEA_VERSION}" \
        postgresql-client && \
    apk del curl

CMD ["stork-agent"]
