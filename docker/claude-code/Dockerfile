# syntax=docker/dockerfile:1
# Claude Code plus the tools this repo's work needs: git, kubectl, flux,
# helm, OpenTofu, the GitHub CLI, Python for scripts/ and its tests,
# PowerShell for scripts/*.ps1, and Ansible for checking ansible/.
#
# Claude Code comes from Anthropic's signed apt repository, so it's a system
# package that never updates itself: rebuild the image (without the build
# cache) to update it. kubectl stays within one minor version of the
# cluster; flux and helm match the versions the cluster and the PC use.
# OpenTofu matches the one in Semaphore's image, which runs the plans: here it
# only formats and validates (tofu init -backend=false). Ansible here only
# parses and checks the playbooks (--syntax-check, a raw task's quoting);
# Semaphore runs them.
#
# Each tool is its own layer with its version ARG right before it, so a Renovate
# bump re-runs only that tool's layer and never the apt layer above. Downloads
# land in a BuildKit cache mount at /dl keyed by their versioned filename, so a
# rebuild - even `--no-cache`, which keeps cache mounts - re-fetches only the
# tools whose version changed and takes the rest from the cache. Checksums are
# verified on every build, cached file or fresh. (Clear a bad cache entry, which
# is rare, with `docker builder prune`.)
FROM debian:trixie-slim

# Claude Code's release channel: stable (about a week old) or latest.
ARG CLAUDE_CODE_CHANNEL=stable

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Base tools, then Claude Code and the GitHub CLI from their own signed
# repositories. The Claude Code key is checked against Anthropic's published
# fingerprint before apt trusts it.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git tmux jq less nano openssh-client \
        dnsutils iputils-ping netcat-openbsd procps python3 python3-venv \
        libicu76 && \
    install -d -m 0755 /etc/apt/keyrings && \
    curl -fsSL https://downloads.claude.ai/keys/claude-code.asc \
        -o /etc/apt/keyrings/claude-code.asc && \
    gpg --show-keys --with-colons /etc/apt/keyrings/claude-code.asc \
        | grep -q '^fpr:::::::::31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE:' && \
    echo "deb [signed-by=/etc/apt/keyrings/claude-code.asc] https://downloads.claude.ai/claude-code/apt/${CLAUDE_CODE_CHANNEL} ${CLAUDE_CODE_CHANNEL} main" \
        > /etc/apt/sources.list.d/claude-code.list && \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends claude-code gh && \
    rm -rf /var/lib/apt/lists/*

# kubectl: the raw binary and its .sha256 (which holds only the hash).
# renovate: datasource=github-releases depName=kubernetes/kubernetes
ARG KUBECTL_VERSION=1.37.2
RUN --mount=type=cache,target=/dl,sharing=locked \
    arch="$(dpkg --print-architecture)" && cd /dl && \
    base="https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${arch}" && \
    f="kubectl-${KUBECTL_VERSION}-${arch}" && \
    { [ -s "$f" ] || { curl -fsSL -o "$f.part" "$base/kubectl" && mv -f "$f.part" "$f"; }; } && \
    echo "$(curl -fsSL "$base/kubectl.sha256")  $f" | sha256sum -c - && \
    install -m 0755 "$f" /usr/local/bin/kubectl

# flux
# renovate: datasource=github-releases depName=fluxcd/flux2
ARG FLUX_VERSION=2.9.6
RUN --mount=type=cache,target=/dl,sharing=locked \
    arch="$(dpkg --print-architecture)" && cd /dl && \
    f="flux_${FLUX_VERSION}_linux_${arch}.tar.gz" && \
    rel="https://github.com/fluxcd/flux2/releases/download/v${FLUX_VERSION}" && \
    { [ -s "$f" ] || { curl -fsSL -o "$f.part" "$rel/${f}" && mv -f "$f.part" "$f"; }; } && \
    curl -fsSL "$rel/flux_${FLUX_VERSION}_checksums.txt" | grep " ${f}\$" | sha256sum -c - && \
    tar -xzf "${f}" -C /usr/local/bin flux

# helm
# renovate: datasource=github-releases depName=helm/helm
ARG HELM_VERSION=4.3.1
RUN --mount=type=cache,target=/dl,sharing=locked \
    arch="$(dpkg --print-architecture)" && cd /dl && \
    f="helm-v${HELM_VERSION}-linux-${arch}.tar.gz" && \
    { [ -s "$f" ] || { curl -fsSL -o "$f.part" "https://get.helm.sh/${f}" && mv -f "$f.part" "$f"; }; } && \
    curl -fsSL "https://get.helm.sh/${f}.sha256sum" | sha256sum -c - && \
    tar -xzf "${f}" --strip-components=1 -C /usr/local/bin "linux-${arch}/helm"

# OpenTofu
# renovate: datasource=github-releases depName=opentofu/opentofu
ARG TOFU_VERSION=1.11.0
RUN --mount=type=cache,target=/dl,sharing=locked \
    arch="$(dpkg --print-architecture)" && cd /dl && \
    f="tofu_${TOFU_VERSION}_linux_${arch}.tar.gz" && \
    rel="https://github.com/opentofu/opentofu/releases/download/v${TOFU_VERSION}" && \
    { [ -s "$f" ] || { curl -fsSL -o "$f.part" "$rel/${f}" && mv -f "$f.part" "$f"; }; } && \
    curl -fsSL "$rel/tofu_${TOFU_VERSION}_SHA256SUMS" | grep " ${f}\$" | sha256sum -c - && \
    tar -xzf "${f}" -C /usr/local/bin tofu

# PowerShell for scripts/*.ps1, best-effort in its own layer: a download or
# checksum failure warns and leaves pwsh out rather than failing the image
# (scripts/*.ps1 can't be checked in it then), while the tools above stay fatal
# on a bad checksum. Its hashes.sha256 is UTF-16 with CRLF, so it's decoded
# (tr strips the NULs and CRs) before the check; grep -a keeps grep in text mode
# past the byte-order mark.
# renovate: datasource=github-releases depName=PowerShell/PowerShell
ARG PWSH_VERSION=7.6.9
RUN --mount=type=cache,target=/dl,sharing=locked \
    arch="$(dpkg --print-architecture)" && cd /dl && \
    pa="$([ "${arch}" = amd64 ] && echo x64 || echo "${arch}")" && \
    f="powershell-${PWSH_VERSION}-linux-${pa}.tar.gz" && \
    rel="https://github.com/PowerShell/PowerShell/releases/download/v${PWSH_VERSION}" && \
    { { [ -s "$f" ] || { curl -fsSL -o "$f.part" "$rel/${f}" && mv -f "$f.part" "$f"; }; } && \
        curl -fsSL "$rel/hashes.sha256" \
            | tr -d '\000\r' | grep -a " \*${f}\$" | sed 's/ \*/  /' | sha256sum -c - && \
        install -d /opt/microsoft/powershell && \
        tar -xzf "${f}" -C /opt/microsoft/powershell && \
        chmod 0755 /opt/microsoft/powershell/pwsh && \
        ln -s /opt/microsoft/powershell/pwsh /usr/local/bin/pwsh && \
        echo "PowerShell ${PWSH_VERSION} installed."; } || \
    echo "WARNING: PowerShell ${PWSH_VERSION} not installed (download or checksum verification failed); scripts/*.ps1 cannot be checked in this image." >&2

# Python packages for scripts/: the same as scripts/requirements.txt and
# scripts/requirements-test.txt; and ansible-core. pip's download cache is a
# BuildKit cache mount, so a rebuild reuses the wheels instead of re-fetching.
RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install 'ruamel.yaml>=0.18' 'pytest>=8.0' 'ansible-core>=2.19'

# Start-up, kubeconfig and credential helpers (see README.md).
COPY bin/ /usr/local/bin/

# The owner of /opt/claude-code/home on the host.
ARG UID=1000
RUN useradd --uid "${UID}" --create-home --shell /bin/bash claude
USER claude
WORKDIR /home/claude
ENV PATH=/opt/venv/bin:$PATH \
    LANG=C.UTF-8 \
    TERM=xterm-256color

ENTRYPOINT ["claude-code-start"]
