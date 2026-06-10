# shopsystem-bc-base — the base image every BC container runs from.
#
# Built and published as ghcr.io/dstengle/shopsystem-bc-base by the
# .github/workflows/publish-bc-base.yml workflow (on "v*" tag push) and
# rebuilt on repository_dispatch by .github/workflows/rebuild-bc-base.yml.
#
# The four dstengle framework utility CLIs (shop-msg via shopsystem-messaging,
# scenarios, shop-templates, and the bc-launcher self-pin) are installed from
# their VCS VERSION PINS in the
#     <pkg> @ git+https://github.com/<owner>/<repo>.git@vMAJOR.MINOR.PATCH
# shape — NOT from an editable clone of a sibling working tree. The owner is
# dstengle. Pinning to a tagged version makes the image content-addressable and
# reproducible: a given bc-base digest always carries the exact utility
# versions named here.
#
# bd (beads) is NOT pip-installable: it is a third-party Go binary (goreleaser
# builds bd), installed from the steveyegge/beads releases in a separate RUN
# block below, pinned to a tagged binary version.
FROM mcr.microsoft.com/devcontainers/python:3.11

USER root

# Framework utility CLIs, version-pinned in the
# "<pkg> @ git+https://github.com/<owner>/<repo>.git@vMAJOR.MINOR.PATCH"
# VCS-pin shape. The owner is dstengle for these four shopsystem packages.
RUN pip install --no-cache-dir \
    "shopsystem-messaging @ git+https://github.com/dstengle/shopsystem-messaging.git@v0.2.1" \
    "scenarios @ git+https://github.com/dstengle/shopsystem-scenarios@v0.2.0" \
    "shop-templates @ git+https://github.com/dstengle/shopsystem-templates.git@v0.2.0" \
    "shopsystem-bc-launcher @ git+https://github.com/dstengle/shopsystem-bc-launcher.git@v0.2.5"

# agent-vault — the in-container broker client (bclaunch-9rr). The agent runs
# wrapped as `agent-vault run -- claude`; agent-vault establishes the proxy
# substitution context so the broker injects the real Claude OAuth / GitHub
# credentials on outbound requests.
#
# agent-vault is an EXTERNAL Infisical project distributed as a Go-binary
# release tarball (NOT pip-installable, NOT a dstengle repo). It is installed
# from the Infisical/agent-vault releases exactly like the bd binary below:
# uname -m → ARCH case, curl download, checksum verification against the
# release checksums.txt, extract, install onto PATH, version sanity check.
#
# The pin is EXPLICIT (v0.32.0), matching the running broker, which reports
# "agent-vault version 0.32.0" (also current latest). 'latest' is NOT used:
# the in-container BC binary must stay broker-compatible.
RUN set -e && \
    ARCH=$(uname -m) && \
    case "$ARCH" in \
        x86_64)  AV_ARCH="amd64" ;; \
        aarch64) AV_ARCH="arm64" ;; \
        *)       echo "Unsupported architecture: $ARCH" && exit 1 ;; \
    esac && \
    AGENT_VAULT_VERSION=0.32.0 && \
    AV_BASE="https://github.com/Infisical/agent-vault/releases/download/v0.32.0" && \
    AV_TARBALL="agent-vault_${AGENT_VAULT_VERSION}_linux_${AV_ARCH}.tar.gz" && \
    curl -fsSL "${AV_BASE}/${AV_TARBALL}" -o "/tmp/${AV_TARBALL}" && \
    curl -fsSL "${AV_BASE}/checksums.txt" -o /tmp/agent-vault-checksums.txt && \
    (cd /tmp && grep " ${AV_TARBALL}\$" agent-vault-checksums.txt | sha256sum -c -) && \
    tar -xzf "/tmp/${AV_TARBALL}" -C /tmp && \
    install -m 0755 /tmp/agent-vault /usr/local/bin/agent-vault && \
    rm -f "/tmp/${AV_TARBALL}" /tmp/agent-vault-checksums.txt /tmp/agent-vault && \
    agent-vault --version

# beads (bd) — third-party Go binary from steveyegge/beads releases (NOT pip-installable)
RUN set -e && \
    ARCH=$(uname -m) && \
    case "$ARCH" in \
        x86_64)  BD_ARCH="amd64" ;; \
        aarch64) BD_ARCH="arm64" ;; \
        *)       echo "Unsupported architecture: $ARCH" && exit 1 ;; \
    esac && \
    BD_VERSION=1.0.3 && \
    BD_URL="https://github.com/steveyegge/beads/releases/download/v${BD_VERSION}/beads_${BD_VERSION}_linux_${BD_ARCH}.tar.gz" && \
    curl -fsSL "$BD_URL" | tar -xz -C /tmp && \
    install -m 0755 /tmp/bd /usr/local/bin/bd && \
    rm -f /tmp/bd && \
    bd --version

# tmux + git are required by the launcher's in-container agent session and
# repository clone steps.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tmux git \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI (gh) — required for a BC container to authenticate git pushes to
# origin/main (the pre-emit "commit reachable from origin/main" gate). The
# launcher already bind-mounts the host's ~/.config/gh into the container
# (controller.py credential propagation), but without `gh` in the image that
# mounted token is unusable and a BC cannot push (lead-ym1b). Installed from the
# official GitHub CLI apt repository.
RUN mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/* \
    && gh --version

# Claude Code CLI — the BC agent runtime. Installed as the vscode user so the
# native installer lands in /home/vscode/.local (where the launched BC, running
# as vscode with HOME=/home/vscode, finds it). Without this the published image
# has no `claude` on PATH and a BC cannot run its agent (lead-79kp). ENV PATH
# adds the install dir for all users at runtime.
USER vscode
RUN curl -fsSL https://claude.ai/install.sh | bash
USER root
ENV PATH="/home/vscode/.local/bin:${PATH}"

# Bake the placeholder Claude credential into the image (bclaunch-9rr,
# lead-v4ih 3931e43e REVISED). Under the operator no-bind-mount directive the
# controller mounts NO credential file; the image instead carries a
# placeholder-only .credentials.json whose accessToken is the literal
# "__PLACEHOLDER__" (NEVER a real OAuth token — the real credential is held
# only by the agent-vault broker, provisioned out of band and substituted on
# outbound requests). vscode-owned so the agent (running as vscode) can read it.
RUN mkdir -p /home/vscode/.claude \
    && printf '%s\n' '{"accessToken":"__PLACEHOLDER__"}' \
       > /home/vscode/.claude/.credentials.json \
    && chown -R vscode:vscode /home/vscode/.claude

# agent-vault broker CA materialization entrypoint (bclaunch-9rr). The script
# is wired BOTH as the image ENTRYPOINT (runs once on container start as PID 1)
# AND installed into /etc/profile.d/ so login/exec shells — notably the
# `docker exec ... agent-vault run -- claude` path, which does NOT inherit the
# entrypoint PID 1's process-local exports — re-materialize the CA from
# AGENT_VAULT_CA_PEM if missing and re-export the five TLS-trust vars.
COPY agent-vault-ca.sh /usr/local/bin/agent-vault-ca.sh
RUN chmod 0755 /usr/local/bin/agent-vault-ca.sh \
    && cp /usr/local/bin/agent-vault-ca.sh /etc/profile.d/agent-vault-ca.sh \
    && chmod 0644 /etc/profile.d/agent-vault-ca.sh

WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/agent-vault-ca.sh"]
CMD ["sleep", "infinity"]
