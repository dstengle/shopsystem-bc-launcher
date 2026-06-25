# shopsystem-bc-lead — the THIN launcher-flavored image: bc-base + the docker CLI.
#
# Built and published as ghcr.io/dstengle/shopsystem-bc-lead by the
# .github/workflows/publish-bc-base.yml workflow (on "v*" tag push), built
# AFTER bc-base is pushed in the same run and FROM that same version.
#
# WHY this image exists (PDR-020 Addendum II, 2026-06-23): the converged lead
# shell's `bc-container launch` shells out to `docker` via subprocess
# (bc_launcher/driver.py), but `docker` is ABSENT from bc-base (verified:
# `command -v docker` fails in ghcr.io/dstengle/shopsystem-bc-base:v0.3.6 →
# FileNotFoundError: 'docker'). The product-authority decision was option (b):
# a SEPARATE thin launcher image rather than baking docker into bc-base,
# because the launcher is the ONLY role that runs docker and adding it to
# bc-base would put a docker-in-docker footprint + host-root-equivalent
# security surface into EVERY lean leaf-BC agent image.
#
# This image adds ONLY the docker CLI (the docker client, NOT the daemon) on
# top of bc-base. Everything else — bc_launcher, bc-container, claude, the
# CA-materialization entrypoint, the baked synthetic ~/.claude state, the four
# framework CLIs, bd, gh, agent-vault — inherits from the bc-base base layer.

# The bc-base version this thin image is FROM is parameterized through a build
# ARG so the publish workflow can build bc-lead:vX FROM bc-base:vX (the SAME
# version being published in that run). The default pins a known-good version
# for a plain (non-CI) build; the workflow overrides it with the pushed tag.
ARG BASE_VERSION=v0.3.6
FROM ghcr.io/dstengle/shopsystem-bc-base:${BASE_VERSION}

USER root

# Install the docker CLI (docker-ce-cli — the docker client, not the
# docker-ce daemon/engine) AND the docker compose plugin (docker-compose-plugin)
# from Docker's official apt repository. The launcher shells out to the `docker`
# client; the daemon is provided by the host (the launcher container talks to the
# host's docker socket). The compose plugin is required because footing
# (lead-ae4h, bin/bootstrap runs footing on this image) runs
# `docker compose -f compose.yaml up -d postgres agent-vault` (footing L172):
# without it `docker compose version` fails with "docker: unknown command:
# docker compose" (lead-ys8x, scenario c5edfa89da00af8a). The same Docker apt
# repo that ships docker-ce-cli also ships docker-compose-plugin, so it is one
# extra package on the existing apt install — keep this THIN.
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
       -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/* \
    && docker --version \
    && docker compose version

# dolt engine binary — required because footing runs `bd dolt push` (the bd
# 1.0.3 client is inherited from bc-base, but `bd dolt` shells out to the dolt
# engine, which is NOT on PATH in bc-base). Without dolt on PATH `bd dolt push`
# fails for a missing engine (lead-ys8x, scenario 98a0683d0360349e). Installed
# as a pinned third-party Go binary from dolthub/dolt releases (NOT
# apt/pip-installable), mirroring the bd install idiom in bc-base. Pinned to a
# specific version for reproducible builds; installed onto /usr/local/bin which
# is on PATH for the vscode user.
RUN set -e && \
    ARCH=$(uname -m) && \
    case "$ARCH" in \
        x86_64)  DOLT_ARCH="amd64" ;; \
        aarch64) DOLT_ARCH="arm64" ;; \
        *)       echo "Unsupported architecture: $ARCH" && exit 1 ;; \
    esac && \
    DOLT_VERSION=1.43.14 && \
    DOLT_URL="https://github.com/dolthub/dolt/releases/download/v${DOLT_VERSION}/dolt-linux-${DOLT_ARCH}.tar.gz" && \
    curl -fsSL "$DOLT_URL" | tar -xz -C /tmp && \
    install -m 0755 "/tmp/dolt-linux-${DOLT_ARCH}/bin/dolt" /usr/local/bin/dolt && \
    rm -rf "/tmp/dolt-linux-${DOLT_ARCH}" && \
    dolt version && \
    command -v dolt

# Reset the default user back to vscode after the root-only docker-CLI install
# (lead-t3dy / lead-fwrx, scenario a4caf0477a74e4bc). bc-base now ends as
# USER vscode so its baked synthetic ~/.claude state resolves for the running
# user; this image switched to USER root ONLY to install docker-ce-cli onto the
# root-owned system paths and MUST restore the vscode default, otherwise the
# published bc-lead Config.User stays root, HOME mismatches /home/vscode, and the
# agent hits first-run onboarding instead of the baked credential/trust state.
# The published bc-lead Config.User is therefore vscode and
# `docker run --rm <image> whoami` reports vscode. The CA-materialization
# entrypoint, HEALTHCHECK and runtime write paths all keep working as vscode
# exactly as they do for bc-base (inherited unchanged).
USER vscode

# Inherit the bc-base WORKDIR, ENTRYPOINT (agent-vault-ca.sh), CMD, HEALTHCHECK
# and ENV unchanged — this image is bc-base plus the docker client, nothing more.
