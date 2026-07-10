"""Fabro def-bundle asset loading + placement script.

Extracted from the former bc_launcher/fabro.py (bead -7pa4 follow-up: fabro
package split). Re-exported via bc_launcher.fabro (the package __init__).

NOTE: the fabro-def assets ship in ``bc_launcher/assets/`` — one directory
ABOVE this subpackage — so the asset root resolves via ``__file__`` with
``.parent.parent`` (this file is ``bc_launcher/fabro/def_bundle.py``).
"""
from __future__ import annotations

import os
from pathlib import Path

from bc_launcher.fabro.constants import *  # noqa: F401,F403  (sibling constants)




def _fabro_def_asset_root() -> Path:
    """Absolute path to the packaged fabro-def asset directory.

    Resolves relative to THIS module so the bundle is found whether the
    launcher runs from a source checkout or an installed wheel (the assets are
    packaged as package data under ``bc_launcher/assets/``).
    """
    return Path(__file__).resolve().parent.parent / FABRO_DEF_ASSET_SUBDIR  # noqa: E501  (parent.parent: assets ship in bc_launcher/, one level above this subpackage)



def _load_fabro_def_files() -> dict[str, bytes]:
    """Read the 15 packaged def-bundle asset files as raw bytes.

    Returns a mapping of def-root-relative path -> file bytes.  Raises
    ``FileNotFoundError`` if any enumerated asset is missing, so a broken
    package surfaces loudly rather than placing a thinner bundle.
    """
    root = _fabro_def_asset_root()
    out: dict[str, bytes] = {}
    for rel in FABRO_DEF_FILES:
        src = root / rel
        out[rel] = src.read_bytes()
    return out



def _fabro_def_install_script(
    files: dict[str, bytes],
    dest_dir: str = FABRO_DEF_CONTAINER_DIR,
) -> str:
    """Build a ``/bin/sh -c`` script that places the def bundle into a container.

    lead-h2bj.  Each file's bytes are base64-encoded on the HOST and decoded on
    the CONTAINER into ``<dest_dir>/<relpath>``, so the placed def is
    byte-identical to the shipped asset regardless of file content (no shell
    quoting/escaping/newline hazards).  The script creates each file's parent
    directory first so the ``nodes/`` and ``vaults/default/`` subtrees are
    reproduced exactly.
    """
    import base64
    import shlex

    lines = ["set -e", f"mkdir -p {shlex.quote(dest_dir)}"]
    for rel in FABRO_DEF_FILES:
        data = files[rel]
        b64 = base64.b64encode(data).decode("ascii")
        target = f"{dest_dir}/{rel}"
        parent = os.path.dirname(target)
        q_target = shlex.quote(target)
        q_parent = shlex.quote(parent)
        lines.append(f"mkdir -p {q_parent}")
        # base64 -d is POSIX-portable on the bc-base image (coreutils).
        lines.append(f"printf %s {shlex.quote(b64)} | base64 -d > {q_target}")
    return "\n".join(lines) + "\n"
