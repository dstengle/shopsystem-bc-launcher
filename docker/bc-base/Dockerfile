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
ARG SHOP_TEMPLATES_VERSION=v0.52.4

# The bc-launcher RELEASE version (the pushed v* tag) is threaded in as a build
# ARG by the publish workflow (build-args: SHOPSYSTEM_BC_LAUNCHER_VERSION=
# ${{ github.ref_name }}). The default below records a known-good value for a
# plain (non-CI) build; the workflow overrides it with the pushed tag. Promoted
# to a persisted ENV below so it surfaces in `docker inspect` (lead-5xnd).
ARG SHOPSYSTEM_BC_LAUNCHER_VERSION=v0.3.72

# Promote both version ARGs to PERSISTED ENV so the published image (and any
# container started from it) surfaces the baked bc-launcher release version and
# baked shop-templates version via `docker image inspect` / `docker container
# inspect` Config.Env — independent of the run-tag the image is addressed by
# (lead-5xnd, scenarios 7c0c949fccdf9df2 / 26d1817c9d115f0d). bc-lead is
# FROM bc-base so it INHERITS these ENV unchanged. A Dockerfile ENV/LABEL is
# inherited via FROM and overrides the misleading upstream devcontainer-base
# org.opencontainers.image.version label value "3.1.2"; the per-build OCI
# version/revision labels are set authoritatively by the workflow `labels:`
# input (build-set labels override inherited base-image labels).
ENV SHOP_TEMPLATES_VERSION=${SHOP_TEMPLATES_VERSION}
ENV SHOPSYSTEM_BC_LAUNCHER_VERSION=${SHOPSYSTEM_BC_LAUNCHER_VERSION}

# Belt-and-suspenders LABEL for the baked shop-templates version: a Dockerfile
# LABEL is inherited via FROM (so bc-lead carries it too) and overrides the
# upstream value. The per-build version/revision labels remain workflow-set.
LABEL shopsystem.shop-templates.version=${SHOP_TEMPLATES_VERSION}

# Framework utility CLIs, version-pinned in the
# "<pkg> @ git+https://github.com/<owner>/<repo>.git@vMAJOR.MINOR.PATCH"
# VCS-pin shape. The owner is dstengle for these four shopsystem packages.
# shop-templates takes its version from the SHOP_TEMPLATES_VERSION build ARG
# (default above) which the centralized scheduled poll bumps in place to the
# resolved latest release (lead-czwo).
# lead-ona9 (scenario 7700eea079ffe1d8): the fabro loop def bundle (formerly
# baked from src/bc_launcher/assets/fabro-def/) is NO LONGER a baked delivery
# surface in this image.  The shopsystem-bc-launcher install below no longer
# carries the def as wheel package-data (pruned in pyproject.toml), and NOTHING
# in this Dockerfile COPYs or bakes the fabro-def tree.  Instead the def is
# delivered at launch by the shop-templates pour (installed just above), which
# emits "/workspace/.fabro/" exactly as it emits ".claude/skills/".  The repo's
# src/bc_launcher/assets/fabro-def/ remains only as the def SOURCE mirror.
RUN pip install --no-cache-dir \
    "shopsystem-messaging @ git+https://github.com/dstengle/shopsystem-messaging.git@v0.4.6" \
    "scenarios @ git+https://github.com/dstengle/shopsystem-scenarios@v0.3.1" \
    "shop-templates @ git+https://github.com/dstengle/shopsystem-templates.git@${SHOP_TEMPLATES_VERSION}" \
    "shopsystem-bc-launcher @ git+https://github.com/dstengle/shopsystem-bc-launcher.git@v0.3.72"

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

# fabro — third-party RUST binary from the fabro-sh/fabro releases (NOT
# pip-installable), baked onto PATH exactly like the bd / agent-vault binaries:
# uname -m → ARCH case, download the pinned release, verify its .sha256 sidecar,
# install onto /usr/local/bin, and a build-time `fabro --version` self-check
# (like the agent-vault / dolt self-checks) so a broken install FAILS the build
# rather than shipping a non-launchable fabro.
#
# ASSET NAMING: fabro is a Rust project publishing TARGET-TRIPLE release assets
# with NO version in the filename (verified via
# `gh api repos/fabro-sh/fabro/releases/tags/v0.254.0 --jq '.assets[].name'`):
#   x86_64 (amd64):  fabro-x86_64-unknown-linux-gnu.tar.gz  (+ .sha256)
#   aarch64 (arm64): fabro-aarch64-unknown-linux-gnu.tar.gz (+ .sha256)
# The tarball nests the binary one level under a fabro-<triple>/ directory
# (fabro-<triple>/fabro), so it is extracted with --strip-components=1. This
# replaces the earlier goreleaser-style guess (fabro_${VER}_linux_${ARCH}.tar.gz)
# which 404'd against fabro's real Rust release assets (bead 0fz).
#
# The pin is PARAMETERIZED through a build ARG (FABRO_VERSION) so the
# centralized scheduled poll (.github/workflows/poll-bc-base-deps.yml) can
# resolve fabro's latest canonical release from fabro-sh/fabro with the
# workflow's OWN GITHUB_TOKEN, bump THIS ARG default in place when newer, commit
# the bump, then rebuild + republish :latest carrying the resolved version. The
# default below records the currently-pinned version (lead-ckq5).
ARG FABRO_VERSION=v0.254.0
RUN set -e && \
    ARCH=$(uname -m) && \
    case "$ARCH" in \
        x86_64)  FABRO_TRIPLE="x86_64-unknown-linux-gnu" ;; \
        aarch64) FABRO_TRIPLE="aarch64-unknown-linux-gnu" ;; \
        *)       echo "Unsupported architecture: $ARCH" && exit 1 ;; \
    esac && \
    FABRO_TARBALL="fabro-${FABRO_TRIPLE}.tar.gz" && \
    FABRO_BASE="https://github.com/fabro-sh/fabro/releases/download/${FABRO_VERSION}" && \
    curl -fsSL "${FABRO_BASE}/${FABRO_TARBALL}" -o "/tmp/${FABRO_TARBALL}" && \
    curl -fsSL "${FABRO_BASE}/${FABRO_TARBALL}.sha256" -o "/tmp/${FABRO_TARBALL}.sha256" && \
    (cd /tmp && sha256sum -c "${FABRO_TARBALL}.sha256") && \
    tar -xzf "/tmp/${FABRO_TARBALL}" -C /tmp --strip-components=1 && \
    install -m 0755 /tmp/fabro /usr/local/bin/fabro && \
    rm -f "/tmp/${FABRO_TARBALL}" "/tmp/${FABRO_TARBALL}.sha256" /tmp/fabro && \
    fabro --version

# The anthropic-oauth-shim — the REAL listening reverse-proxy shim for the
# brokered Anthropic OAuth path (lead-so2h, replacing the earlier exec-wrapper
# baked under lead-ckq5). It is a committed stdlib-ONLY python3 ThreadingHTTPServer
# reverse proxy (no third-party import required to run it): it BINDS + LISTENS on
# 127.0.0.1:8788 (fabro's ADR-049 D2 base_url http://127.0.0.1:8788/v1), and per
# request STRIPS the incoming x-api-key + authorization, ADDS
# `Authorization: Bearer <dummy>` + `anthropic-beta: oauth-2025-04-20`, and
# forwards upstream to https://api.anthropic.com via the env HTTPS_PROXY so
# agent-vault injects the real OAuth credential on the wire (NO real secret is
# baked — DUMMY_BEARER is a placeholder). COPIED onto PATH and made executable so
# it is resolvable inside the running container as `anthropic-oauth-shim`, with a
# build-time `--help` self-check (argparse exits zero) so a broken install FAILS
# the build. It is a real artifact, not a placeholder/stub.
COPY anthropic-oauth-shim /usr/local/bin/anthropic-oauth-shim
RUN chmod 0755 /usr/local/bin/anthropic-oauth-shim \
    && anthropic-oauth-shim --help >/dev/null

# beads (bd) — third-party Go binary from steveyegge/beads releases (NOT pip-installable)
RUN set -e && \
    ARCH=$(uname -m) && \
    case "$ARCH" in \
        x86_64)  BD_ARCH="amd64" ;; \
        aarch64) BD_ARCH="arm64" ;; \
        *)       echo "Unsupported architecture: $ARCH" && exit 1 ;; \
    esac && \
    BD_VERSION=1.1.0 && \
    BD_URL="https://github.com/steveyegge/beads/releases/download/v${BD_VERSION}/beads_${BD_VERSION}_linux_${BD_ARCH}.tar.gz" && \
    curl -fsSL "$BD_URL" | tar -xz -C /tmp && \
    install -m 0755 /tmp/bd /usr/local/bin/bd && \
    rm -f /tmp/bd && \
    bd --version

# tmux + git are required by the launcher's in-container agent session and
# repository clone steps. tini is a minimal reaping init: it becomes PID 1 (via
# the ENTRYPOINT wrap below) and reaps orphaned <defunct> children that reparent
# to PID 1 when their parents exit unreaped. Without it, in-container tooling
# (bd, git, fabro, gh, shop-msg) leaks zombies forever — an --orchestrator fabro
# BC was observed accumulating 540 zombies (532 bd) in ~15 minutes because the
# ADR-058 dispatcher poll spawns bd every ~5s (lead-xnop).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tmux git tini \
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

# /workspace ownership (lead-uiwu FACET 2, scenario 4154b0ea63d0516b).
#
# REGRESSION FIX: a bare `WORKDIR /workspace` creates the directory root:root.
# The agent — and the launch-time in-container clone — run as the unprivileged
# vscode user (uid 1000), so a clone into a root-owned /workspace fails
# "git clone failed: ... /workspace/.git: Permission denied".  The working
# (5-day-old) messaging container has /workspace owned vscode:vscode.  Create
# /workspace and hand it to vscode DURABLY here so the non-root clone succeeds
# without a host-side `docker exec -u root chown vscode:vscode /workspace`.
# This must come BEFORE the `USER vscode` switch below (chown requires root).
RUN mkdir -p /workspace \
    && chown vscode:vscode /workspace
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

# tini is PID 1 (a reaping init) and WRAPS — does not replace — the
# CA-materialization entrypoint. tini reaps orphaned <defunct> children
# regardless of run flags (covers the clone-path AND the workspace-mount
# launch), then execs agent-vault-ca.sh as its child so the broker CA is still
# materialized and the five trust vars exported on container start; that script
# in turn execs CMD (`sleep infinity`) to keep the container alive. This is the
# fix for the PID1-has-no-reaper zombie leak (lead-xnop). The agent-vault CA
# entrypoint remains the CA entrypoint — the pinned agent-vault entrypoint
# scenario still holds.
ENTRYPOINT ["tini", "--", "/usr/local/bin/agent-vault-ca.sh"]
CMD ["sleep", "infinity"]
