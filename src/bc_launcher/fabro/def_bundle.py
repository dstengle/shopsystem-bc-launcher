"""Fabro def-bundle asset loading + placement script.

Extracted from the former bc_launcher/fabro.py (bead -7pa4 follow-up: fabro
package split). Re-exported via bc_launcher.fabro (the package __init__).

NOTE: the fabro-def assets ship in ``bc_launcher/assets/`` — one directory
ABOVE this subpackage — so the asset root resolves via ``__file__`` with
``.parent.parent`` (this file is ``bc_launcher/fabro/def_bundle.py``).
"""
from __future__ import annotations

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
    dest_dir: str = FABRO_DEF_CONTAINER_DIR,
) -> str:
    """Build the FIXED, tiny ``/bin/sh -c`` script that unpacks the def bundle.

    lead-m4zt (E2BIG fix).  The prior form INLINED every file's base64 into the
    script, making the ``/bin/sh -c`` argument grow with the bundle — the real
    18-file bundle already exceeds 136 KiB, past the Linux MAX_ARG_STRLEN 128
    KiB per-single-argument limit, so the placement exec failed the spawn with
    E2BIG ("Argument list too long") and the container never got its def.

    The bundle bytes now travel on STDIN (``docker exec -i``) as a
    base64-encoded tar (see ``_fabro_def_bundle_tar_b64``); this script is a
    CONSTANT that reads that stream, base64-decodes it, and untars it into
    ``dest_dir``.  Because the script carries NO file content its length is
    fixed and tiny regardless of bundle size — the placement is immune to the
    per-argument limit at any bundle size.  Each file lands byte-identical to
    the shipped asset (tar preserves content verbatim), reproducing the
    ``nodes/`` and ``vaults/default/`` subtrees exactly, and the native
    ``vaults/default/secrets.json`` remains the ``__PLACEHOLDER__``-only asset
    (ADR-049).  ``base64 -d`` and ``tar`` are POSIX-portable on the bc-base
    image (coreutils + tar).
    """
    import shlex

    q_dest = shlex.quote(dest_dir)
    return (
        "set -e\n"
        f"mkdir -p {q_dest}\n"
        f"base64 -d | tar -x -f - -C {q_dest}\n"
    )


def _fabro_def_bundle_tar_b64(files: dict[str, bytes]) -> str:
    """Build the base64-encoded tar stream of the def bundle for STDIN placement.

    lead-m4zt.  Packs the 18 def-root files into a deterministic, reproducible
    (mtime/uid/gid zeroed) tar, base64-encoded to a plain ASCII string so it
    streams cleanly through ``docker exec -i`` STDIN.  Each entry carries its
    def-root-relative path so ``tar -x`` reproduces the ``nodes/`` and
    ``vaults/default/`` subtrees under the placement dir.  The content is
    written into the tar verbatim, so every placed file is byte-identical to
    the shipped asset — the placement introduces no shell-quoting/escaping or
    newline hazard, and no real secret (the vault stays placeholder-only).
    """
    import base64
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel in FABRO_DEF_FILES:
            data = files[rel]
            info = tarfile.TarInfo(name=rel)
            info.size = len(data)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode("ascii")
