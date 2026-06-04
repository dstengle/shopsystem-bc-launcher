# shopsystem-bc-base — the base image every BC container runs from.
#
# Built and published as ghcr.io/dstengle/shopsystem-bc-base by the
# .github/workflows/publish-bc-base.yml workflow (on "v*" tag push) and
# rebuilt on repository_dispatch by .github/workflows/rebuild-bc-base.yml.
#
# The framework utility CLIs (shop-msg via shopsystem-messaging, scenarios,
# shop-templates, bd/beads, and the bc-launcher self-pin) are installed from
# their VCS VERSION PINS in the
#     <pkg> @ git+https://github.com/<owner>/<repo>.git@vMAJOR.MINOR.PATCH
# shape — NOT from an editable clone of a sibling working tree. The common
# owner is dstengle; beads is the legitimate third-party gascity/beads source.
# Pinning to a tagged version makes the image content-addressable and
# reproducible: a given bc-base digest always carries the exact utility
# versions named here.
FROM mcr.microsoft.com/devcontainers/python:3.11

USER root

# Framework utility CLIs, version-pinned in the
# "<pkg> @ git+https://github.com/<owner>/<repo>.git@vMAJOR.MINOR.PATCH"
# VCS-pin shape. The owner is dstengle for the shopsystem packages; beads
# installs from the third-party gascity/beads source.
RUN pip install --no-cache-dir \
    "shopsystem-messaging @ git+https://github.com/dstengle/shopsystem-messaging.git@v0.2.0" \
    "scenarios @ git+https://github.com/dstengle/shopsystem-scenarios.git@v0.2.0" \
    "shop-templates @ git+https://github.com/dstengle/shopsystem-templates.git@v0.2.0" \
    "beads @ git+https://github.com/gascity/beads.git@v0.61.0" \
    "shopsystem-bc-launcher @ git+https://github.com/dstengle/shopsystem-bc-launcher.git@v0.2.0"

# tmux + git are required by the launcher's in-container agent session and
# repository clone steps.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tmux git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

CMD ["sleep", "infinity"]
