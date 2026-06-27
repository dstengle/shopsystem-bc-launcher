# shopsystem-bc-base — the base image every BC container runs from.
#
# Built and published as ghcr.io/dstengle/shopsystem-bc-base by the
# .github/workflows/publish-bc-base.yml workflow (on "v*" tag push), rebuilt on
# a bare "rebuild-bc-base" repository_dispatch by
# .github/workflows/rebuild-bc-base.yml, and dependency-bumped + rebuilt on a
# recurring schedule by the centralized poll
# .github/workflows/poll-bc-base-deps.yml (lead-czwo / ADR-022).
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

# The shop-templates VCS version is PARAMETERIZED through a build ARG rather
# than frozen at a hard-coded vMAJOR.MINOR.PATCH literal. The centralized
# scheduled poll (lead-czwo, .github/workflows/poll-bc-base-deps.yml) resolves
# shop-templates' latest canonical release and, when newer than this default,
# bumps THIS ARG default in place, commits the bumped Dockerfile, then rebuilds
# and republishes "latest" so it carries the resolved version. The default
# below records the currently-pinned version.
#
# (ADR-022 / lead-czwo: this replaces the retired shopsystem-templates-release
# repository_dispatch fan-in, which previously threaded the released tag in via
# --build-arg from a dispatch client_payload.)
ARG SHOP_TEMPLATES_VERSION=v0.44.0

# Framework utility CLIs, version-pinned in the
# "<pkg> @ git+https://github.com/<owner>/<repo>.git@vMAJOR.MINOR.PATCH"
# VCS-pin shape. The owner is dstengle for these four shopsystem packages.
# shop-templates takes its version from the SHOP_TEMPLATES_VERSION build ARG
# (default above) which the centralized scheduled poll bumps in place to the
# resolved latest release (lead-czwo).
RUN pip install --no-cache-dir \
    "shopsystem-messaging @ git+https://github.com/dstengle/shopsystem-messaging.git@v0.4.0" \
    "scenarios @ git+https://github.com/dstengle/shopsystem-scenarios@v0.2.0" \
    "shop-templates @ git+https://github.com/dstengle/shopsystem-templates.git@${SHOP_TEMPLATES_VERSION}" \
    "shopsystem-bc-launcher @ git+https://github.com/dstengle/shopsystem-bc-launcher.git@v0.3.31"

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

# Bake the synthetic logged-in Claude state into the image (bclaunch-2s6y,
# supersedes the bare-shape bake of bclaunch-9rr / lead-v4ih 3931e43e). Under
# the operator no-bind-mount brokered model the controller mounts NO credential
# file; the image carries a placeholder-only, SYNTHETIC logged-in state so
# claude boots STRAIGHT to the agent (no first-run wizard) — the broker
# substitutes the real Authorization on the wire, so NO real OAuth token is ever
# baked here.
#
# Two files, ALL placeholder/synthetic:
#
# (1) ~/.claude/.credentials.json — the NESTED claudeAiOauth stanza claude
#     actually recognizes as logged-in. The prior BARE {"accessToken":...} shape
#     was wrong: claude never recognized itself as logged in and sat at the
#     login-method picker. accessToken/refreshToken are the literal
#     "__PLACEHOLDER__"; expiresAt is far-future (year ~2099) so claude never
#     attempts a refresh; the broker swaps the Authorization header regardless.
#
# (2) ~/.claude.json — the onboarding/trust state that skips the first-run
#     wizard gates (theme -> login-method -> folder-trust -> bypass-permissions).
#     hasCompletedOnboarding ALONE does NOT skip the login-method picker, hence
#     (1). The bypass-permissions acceptance gate (the
#     `claude --dangerously-skip-permissions` warning) is the top-level key
#     "bypassPermissionsModeAccepted" — confirmed by grepping the claude
#     2.1.170 binary (read as !S$().bypassPermissionsModeAccepted from the global
#     ~/.claude.json config). lastOnboardingVersion is pinned to the installed
#     claude version (2.1.170). oauthAccount + userID are synthetic placeholders.
#     The /workspace project stanza is pre-trusted so no folder-trust prompt
#     fires. vscode-owned so the agent (running as vscode) can read it.
RUN mkdir -p /home/vscode/.claude \
    && printf '%s\n' '{"claudeAiOauth":{"accessToken":"__PLACEHOLDER__","refreshToken":"__PLACEHOLDER__","expiresAt":4070908800000,"scopes":["user:file_upload","user:inference","user:mcp_servers","user:profile","user:sessions:claude_code"],"subscriptionType":"max"}}' \
       > /home/vscode/.claude/.credentials.json \
    && printf '%s\n' '{"hasCompletedOnboarding":true,"bypassPermissionsModeAccepted":true,"theme":"dark","lastOnboardingVersion":"2.1.170","userID":"__PLACEHOLDER__","oauthAccount":{"accountUuid":"__PLACEHOLDER__","emailAddress":"__PLACEHOLDER__","organizationUuid":"__PLACEHOLDER__","organizationName":"__PLACEHOLDER__","organizationRole":"admin","subscriptionType":"max"},"projects":{"/workspace":{"hasTrustDialogAccepted":true,"hasCompletedProjectOnboarding":true,"projectOnboardingSeenCount":0,"allowedTools":[]}}}' \
       > /home/vscode/.claude.json \
    && chown -R vscode:vscode /home/vscode/.claude /home/vscode/.claude.json

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

# Interactive BOOTSTRAP entrypoint mode (lead-f6xs). This is a MODE of THIS
# existing bc-base lineage image — NOT a separate, purpose-built bootstrap
# image. The same image that runs the brokered steady-state agent
# (`agent-vault run -- claude`) is also run with this bootstrap entrypoint
# selected to perform the one-time HUMAN authentication beat: it invokes
# `claude` and `gh auth login` INTERACTIVELY attached to the host TTY so a
# human authenticates directly, NOT wrapped as `agent-vault run -- claude`, and
# it places NO "__PLACEHOLDER__" credential. Because it ships in this same
# image, the four baked framework CLIs (shop-templates, shop-msg, bc-container,
# agent-vault) resolve on PATH inside a bootstrap-mode container exactly as for
# a brokered steady-state run. Selected at `docker run` time by overriding the
# entrypoint to /usr/local/bin/bootstrap-entrypoint.sh (with a TTY allocated,
# e.g. `docker run -it --entrypoint /usr/local/bin/bootstrap-entrypoint.sh`).
COPY bootstrap-entrypoint.sh /usr/local/bin/bootstrap-entrypoint.sh
RUN chmod 0755 /usr/local/bin/bootstrap-entrypoint.sh

# In-container HEALTHCHECK probe (bclaunch-wuo). The script TCP-probes the two
# services a BC strictly depends on — the agent-vault broker (host:port parsed
# from the in-container HTTPS_PROXY proxy-listener address, fallback
# AGENT_VAULT_ADDR) and the messaging database (host:port parsed from
# SHOPMSG_DSN) — and exits 0 (healthy) / 1 (unhealthy). Docker surfaces that
# exit code as .State.Health.Status, which the host reads via docker inspect.
# Without this instruction the image carried no .State.Health and docker
# inspect reported "none", so the unhealthy-when-broker-down behavior was
# fake-driver-only. The probe targets are RUNTIME env (operator-supplied at
# `docker run -e`), so they are read at probe time, not baked.
COPY bc-healthcheck.sh /usr/local/bin/bc-healthcheck.sh
RUN chmod 0755 /usr/local/bin/bc-healthcheck.sh
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD ["/usr/local/bin/bc-healthcheck.sh"]

WORKDIR /workspace

# Default to the vscode user so the running user's HOME is /home/vscode and the
# baked synthetic logged-in Claude state (/home/vscode/.claude/.credentials.json
# + /home/vscode/.claude.json, written vscode-owned above) RESOLVES for the
# running user — claude boots straight to the agent instead of hitting the
# first-run onboarding / login-method picker that a root HOME (/root) mismatch
# produced (lead-t3dy / lead-fwrx, scenario a4caf0477a74e4bc). The published
# bc-base Config.User is therefore vscode and `docker run --rm <image> whoami`
# reports vscode.
#
# This must come AFTER every privileged build step above (apt installs, the
# COPY+chmod of the entrypoint/healthcheck/bootstrap scripts onto root-owned
# /usr/local/bin). It does NOT break container start as the default vscode user:
#   * The CA-materialization ENTRYPOINT (agent-vault-ca.sh) writes ONLY under
#     /home/vscode/.config/agent-vault — the vscode user's own HOME subtree, so
#     no root-owned trust store is touched and no update-ca-certificates is run
#     (trust is via the five per-process CA env vars, not the system store). To
#     guarantee the entrypoint's `mkdir -p /home/vscode/.config/agent-vault`
#     succeeds as vscode, pre-create /home/vscode/.config vscode-owned here.
#   * The HEALTHCHECK (bc-healthcheck.sh) only runs unprivileged python3 TCP
#     probes, which need no root.
#   * No root-owned runtime write path remains: the only paths the runtime
#     writes (the baked ~/.claude state and the CA dir) are vscode-owned.
RUN mkdir -p /home/vscode/.config \
    && chown -R vscode:vscode /home/vscode/.config
USER vscode

ENTRYPOINT ["/usr/local/bin/agent-vault-ca.sh"]
CMD ["sleep", "infinity"]
