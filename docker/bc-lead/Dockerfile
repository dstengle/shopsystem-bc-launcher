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

# Install ONLY the docker CLI (docker-ce-cli — the docker client, not the
# docker-ce daemon/engine) from Docker's official apt repository. The launcher
# only ever shells out to the `docker` client; the daemon is provided by the
# host (the launcher container talks to the host's docker socket). Keep this
# THIN: nothing beyond what is required to install the docker client.
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
       -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/* \
    && docker --version

# Inherit the bc-base WORKDIR, ENTRYPOINT (agent-vault-ca.sh), CMD, HEALTHCHECK
# and ENV unchanged — this image is bc-base plus the docker client, nothing more.
