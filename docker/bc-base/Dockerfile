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
    "shopsystem-bc-launcher @ git+https://github.com/dstengle/shopsystem-bc-launcher.git@v0.2.0"

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

WORKDIR /workspace

CMD ["sleep", "infinity"]
