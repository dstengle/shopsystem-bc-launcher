# shopsystem-bc-base — the base image every BC container runs from.
#
# Built and published as ghcr.io/dstengle/shopsystem-bc-base by the
# .github/workflows/publish-bc-base.yml workflow (on "v*" tag push) and
# rebuilt on repository_dispatch by .github/workflows/rebuild-bc-base.yml.
#
# The framework utility CLIs (shop-msg, bd/beads) are installed from their
# VCS / published-package VERSION PINS in the
#     github.com/dstengle/<utility> @ vMAJOR.MINOR.PATCH
# shape — NOT from an editable clone of a sibling working tree. Pinning to a
# tagged version makes the image content-addressable and reproducible: a given
# bc-base digest always carries the exact utility versions named here.
FROM mcr.microsoft.com/devcontainers/python:3.11

USER root

# Framework utility CLIs, version-pinned in the
# "github.com/dstengle/<utility> @ vMAJOR.MINOR.PATCH" VCS-pin shape.
# pip's VCS requirement spelling for "github.com/dstengle/<utility> @ vX.Y.Z"
# is "<utility> @ git+https://github.com/dstengle/<utility>.git@vX.Y.Z".
RUN pip install --no-cache-dir \
    "shop-msg @ git+https://github.com/dstengle/shop-msg.git@v0.3.0" \
    "beads @ git+https://github.com/dstengle/beads.git@v0.5.1"

# tmux + git are required by the launcher's in-container agent session and
# repository clone steps.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tmux git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

CMD ["sleep", "infinity"]
