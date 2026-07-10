"""Support helpers/constants: lead (extracted from tests/conftest.py).

Plain imported module (NOT a pytest plugin). Domain boundaries are
organizational; step modules import what they reference from here.
"""
from __future__ import annotations

import re
from tests.support.common import _find_bc_lead_dockerfile, _strip_dockerfile_comments  # noqa: F401


def _bc_lead_dockerfile_text(ctx) -> str:
    """Resolve and cache the committed bc-lead Dockerfile text for these steps."""
    cached = ctx.get("footing_toolset_text")
    if cached is not None:
        return cached
    path = _find_bc_lead_dockerfile()
    assert path is not None, (
        "No tracked Dockerfile found that builds shopsystem-bc-lead "
        "(FROM ...shopsystem-bc-base). The footing toolset scenarios "
        "(lead-ys8x) bind to that Dockerfile's content."
    )
    ctx["footing_toolset_path"] = path
    text = path.read_text()
    ctx["footing_toolset_text"] = text
    return text


def _bc_lead_installs_compose_plugin(text: str) -> bool:
    """True iff the bc-lead Dockerfile INSTALLS the docker compose plugin.

    The compose plugin ships as the `docker-compose-plugin` apt package from
    Docker's official apt repo (the same repo that provides docker-ce-cli), so
    its presence in an apt(-get) install instruction is the buildable-artifact
    proof that `docker compose` resolves in the published image. We match only
    a non-comment `apt[-get] install ... docker-compose-plugin` line so a mere
    comment mention does not satisfy the teeth.
    """
    instructions = _strip_dockerfile_comments(text)
    return bool(re.search(
        r"apt(?:-get)?\s+install\b[^\n]*\bdocker-compose-plugin\b", instructions))


def _bc_lead_installs_dolt_on_path(text: str) -> bool:
    """True iff the bc-lead Dockerfile INSTALLS the dolt binary onto PATH.

    The dolt engine is a third-party Go binary (not apt/pip installable); the
    Dockerfile installs it from the dolthub/dolt releases onto /usr/local/bin
    (on PATH). We require, in NON-comment instructions, that dolt is placed on a
    PATH location (install/cp/mv into a bin dir, or an explicit
    /usr/local/bin/dolt target) so a comment mention does not satisfy the teeth.
    """
    instructions = _strip_dockerfile_comments(text)
    return bool(
        re.search(
            r"(install|cp|mv)\b[^\n]*\bdolt\b[^\n]*/usr/local/bin",
            instructions,
        )
        or re.search(r"/usr/local/bin/dolt\b", instructions)
    )
