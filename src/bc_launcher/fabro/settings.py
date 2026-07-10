"""Fabro settings.toml + workflow.toml (re)write script builders.

Extracted from the former bc_launcher/fabro.py (bead -7pa4 follow-up: fabro
package split). Re-exported via bc_launcher.fabro (the package __init__).
"""
from __future__ import annotations

import os
import re

from bc_launcher.fabro.constants import *  # noqa: F401,F403  (sibling constants)
from bc_launcher.fabro.def_bundle import _fabro_def_asset_root




def _fabro_settings_toml() -> str:
    """The effective fabro settings TOML the launcher writes on the fabro path.

    Carries ``[llm.providers.anthropic]`` with ``base_url`` pointed at the
    local shim and ``adapter = "anthropic"`` (native format, no translation).
    Writes NO credential slot — the real Anthropic credential rides
    agent-vault on the wire, never fabro's settings (ADR-049 D1/D2).
    """
    return (
        "# settings.toml -- EFFECTIVE fabro settings written by the "
        "shopsystem-bc-launcher\n"
        "# fabro orchestrator launch path (lead-vwib).  Points fabro's "
        "built-in\n"
        "# anthropic provider at the in-container anthropic-oauth-shim "
        "(lead-so2h)\n"
        f"# listening on {FABRO_SHIM_HOST}:{FABRO_SHIM_PORT}.  The adapter "
        'stays "anthropic"\n'
        "# so the shim speaks native Anthropic Messages format in both "
        "directions;\n"
        "# NO OpenAI<->Anthropic translation adapter is introduced "
        "(ADR-049 D2).\n"
        "#\n"
        "# ADR-049 D1: NO real credential is written here.  The real "
        "Anthropic\n"
        "# credential rides ONLY the agent-vault surface on the wire via the\n"
        "# container HTTPS_PROXY; fabro's native vault stays "
        '"__PLACEHOLDER__"-only.\n'
        "\n"
        "[llm.providers.anthropic]\n"
        f'base_url = "{FABRO_ANTHROPIC_BASE_URL}"\n'
        f'adapter = "{FABRO_ANTHROPIC_ADAPTER}"\n'
    )



def _fabro_settings_install_script(
    dest_path: str = FABRO_SETTINGS_CONTAINER_PATH,
) -> str:
    """Build a ``/bin/sh -c`` script that writes the effective fabro settings
    into the placed def at ``dest_path``.

    The TOML bytes are base64-encoded on the HOST and decoded on the
    CONTAINER (same byte-safe channel the def-bundle placement uses), so the
    written settings are byte-identical to ``_fabro_settings_toml()``
    regardless of content.
    """
    import base64
    import shlex

    data = _fabro_settings_toml().encode("utf-8")
    b64 = base64.b64encode(data).decode("ascii")
    q_target = shlex.quote(dest_path)
    q_parent = shlex.quote(os.path.dirname(dest_path))
    return (
        "set -e\n"
        f"mkdir -p {q_parent}\n"
        f"printf %s {shlex.quote(b64)} | base64 -d > {q_target}\n"
    )



def _fabro_workflow_toml_rewrite(source: str, bc_name: str, work_id: str) -> str:
    """Rewrite the packaged workflow.toml's BC_NAME / WORK_ID to the launch's
    ACTUAL values (lead-ze4w BUG#2).

    The packaged asset ships BC_NAME / WORK_ID in TWO tables:
      * ``[run.inputs]``          — the agent-prompt inputs (`fabro run -I`
                                    overrides these, but only for prompts);
      * ``[run.environment.env]`` — the env overlay that reaches the native
                                    ``script=`` sandbox as real shell env vars
                                    ($BC_NAME / $WORK_ID), which `-I` does NOT
                                    override.

    Both carry the bundle defaults (``fabro-throwaway`` /
    ``fabro-spike-demo-3``).  This rewrites EVERY ``BC_NAME = "..."`` and
    ``WORK_ID = "..."`` assignment (in either table) to the launch's actual
    ``bc_name`` / ``work_id``, so the native nodes run against the real
    identity rather than the bundle default.  Modeled on the settings.toml
    (re)write: the corrected bytes are produced on the host and written over
    the placed file.
    """
    def _sub(line: str) -> str:
        # Match a top-of-line TOML key assignment `KEY = "value"` (optional
        # trailing comment preserved), for BC_NAME / WORK_ID only.
        m = re.match(
            r'^(?P<key>BC_NAME|WORK_ID)(?P<sp>\s*=\s*)"[^"]*"(?P<rest>.*)$',
            line,
        )
        if not m:
            return line
        value = bc_name if m.group("key") == "BC_NAME" else work_id
        # Escape any embedded double-quote / backslash so the emitted TOML
        # string stays well-formed regardless of the identity value.
        safe = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'{m.group("key")}{m.group("sp")}"{safe}"{m.group("rest")}'

    return "\n".join(_sub(line) for line in source.split("\n"))



def _fabro_workflow_toml_install_script(
    bc_name: str,
    work_id: str,
    dest_path: str = FABRO_WORKFLOW_TOML_CONTAINER_PATH,
) -> str:
    """Build a ``/bin/sh -c`` script that (re)writes the placed workflow.toml
    with the launch's ACTUAL BC_NAME / WORK_ID (lead-ze4w BUG#2).

    Reads the packaged workflow.toml asset, rewrites the BC_NAME / WORK_ID
    assignments in ``[run.inputs]`` and ``[run.environment.env]`` to the
    launch's values, then base64-decode-writes the corrected bytes over the
    placed ``workflow.toml`` — the SAME byte-safe channel + overwrite
    mechanism the launcher uses to (re)write settings.toml.
    """
    import base64
    import shlex

    asset = (_fabro_def_asset_root() / "workflow.toml").read_text(
        encoding="utf-8"
    )
    rewritten = _fabro_workflow_toml_rewrite(asset, bc_name, work_id)
    b64 = base64.b64encode(rewritten.encode("utf-8")).decode("ascii")
    q_target = shlex.quote(dest_path)
    q_parent = shlex.quote(os.path.dirname(dest_path))
    return (
        "set -e\n"
        f"mkdir -p {q_parent}\n"
        f"printf %s {shlex.quote(b64)} | base64 -d > {q_target}\n"
    )
