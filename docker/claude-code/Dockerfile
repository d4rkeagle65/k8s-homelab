# Claude Code plus the tools this repo's work needs: git, kubectl, flux,
# helm, OpenTofu, the GitHub CLI, and Python for scripts/ and its tests.
#
# Claude Code comes from Anthropic's signed apt repository, so it's a system
# package that never updates itself: rebuild the image (without the build
# cache) to update it. kubectl stays within one minor version of the
# cluster; flux and helm match the versions the cluster and the PC use.
# OpenTofu matches the one in Semaphore's image, which runs the plans: here it
# only formats and validates (tofu init -backend=false).
FROM debian:trixie-slim

ARG KUBECTL_VERSION=1.36.5
ARG FLUX_VERSION=2.9.6
ARG HELM_VERSION=4.2.2
ARG TOFU_VERSION=1.11.0
# Claude Code's release channel: stable (about a week old) or latest.
ARG CLAUDE_CODE_CHANNEL=stable
# The owner of /opt/claude-code/home on the host.
ARG UID=1000

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Base tools, then Claude Code and the GitHub CLI from their own signed
# repositories. The Claude Code key is checked against Anthropic's published
# fingerprint before apt trusts it.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git tmux jq less nano openssh-client \
        dnsutils iputils-ping netcat-openbsd procps python3 python3-venv && \
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

# kubectl, flux, helm and tofu, each checked against its published checksum.
RUN arch="$(dpkg --print-architecture)" && cd /tmp && \
    curl -fsSLO "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${arch}/kubectl" && \
    echo "$(curl -fsSL "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${arch}/kubectl.sha256")  kubectl" \
        | sha256sum -c - && \
    install -m 0755 kubectl /usr/local/bin/kubectl && \
    flux="flux_${FLUX_VERSION}_linux_${arch}.tar.gz" && \
    curl -fsSLO "https://github.com/fluxcd/flux2/releases/download/v${FLUX_VERSION}/${flux}" && \
    curl -fsSL "https://github.com/fluxcd/flux2/releases/download/v${FLUX_VERSION}/flux_${FLUX_VERSION}_checksums.txt" \
        | grep " ${flux}\$" | sha256sum -c - && \
    tar -xzf "${flux}" -C /usr/local/bin flux && \
    helm="helm-v${HELM_VERSION}-linux-${arch}.tar.gz" && \
    curl -fsSLO "https://get.helm.sh/${helm}" && \
    curl -fsSL "https://get.helm.sh/${helm}.sha256sum" | sha256sum -c - && \
    tar -xzf "${helm}" --strip-components=1 -C /usr/local/bin "linux-${arch}/helm" && \
    tofu="tofu_${TOFU_VERSION}_linux_${arch}.tar.gz" && \
    curl -fsSLO "https://github.com/opentofu/opentofu/releases/download/v${TOFU_VERSION}/${tofu}" && \
    curl -fsSL "https://github.com/opentofu/opentofu/releases/download/v${TOFU_VERSION}/tofu_${TOFU_VERSION}_SHA256SUMS" \
        | grep " ${tofu}\$" | sha256sum -c - && \
    tar -xzf "${tofu}" -C /usr/local/bin tofu && \
    rm -f /tmp/kubectl /tmp/*.tar.gz

# Python packages for scripts/: the same as scripts/requirements.txt and
# scripts/requirements-test.txt.
RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir 'ruamel.yaml>=0.18' 'pytest>=8.0'

# Start-up, kubeconfig and credential helpers (see README.md).
COPY bin/ /usr/local/bin/

RUN useradd --uid "${UID}" --create-home --shell /bin/bash claude
USER claude
WORKDIR /home/claude
ENV PATH=/opt/venv/bin:$PATH \
    LANG=C.UTF-8 \
    TERM=xterm-256color

ENTRYPOINT ["claude-code-start"]
