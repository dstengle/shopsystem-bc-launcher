"""Support helpers/constants: common (extracted from tests/conftest.py).

Plain imported module (NOT a pytest plugin). Domain boundaries are
organizational; step modules import what they reference from here.
"""
from __future__ import annotations

from pathlib import Path
import re
from tests.conftest import _REPO_ROOT  # noqa: F401


_REAL_OAUTH_TOKEN = "sk-ant-REAL-oauth-accessToken-DO-NOT-LEAK"


_BAKED_CREDENTIALS_PATH = "/home/vscode/.claude/.credentials.json"


def _baked_credentials_json() -> dict:
    """Parse the FULL .credentials.json JSON the bc-base Dockerfile bakes.

    bclaunch-2s6y: the Dockerfile now bakes the NESTED claudeAiOauth stanza at
    /home/vscode/.claude/.credentials.json (the prior bare {"accessToken":...}
    shape was wrong — claude never recognized itself as logged in).  We recover
    the EXACT JSON object the image will carry by locating the
    `> /home/vscode/.claude/.credentials.json` redirect in the committed
    Dockerfile and JSON-parsing the single-quoted JSON literal that precedes it
    (docker build is NOT run — docker is unavailable).  Parsing the real JSON
    (not regexing one field) gives the nested-shape assertions teeth: a bare
    top-level accessToken would fail to expose the nested claudeAiOauth path.
    """
    import json as _json
    import re as _re

    dockerfile = _find_bc_base_dockerfile()
    text = dockerfile.read_text() if dockerfile else ""
    # Find the printf '<json>' ... > .../.credentials.json bake line.  The JSON
    # literal is single-quoted in the Dockerfile.
    m = _re.search(
        r"printf\s+'%s\\n'\s+'(\{.*?\})'\s*\\?\s*\n\s*>\s*"
        r"/home/vscode/\.claude/\.credentials\.json\b",
        text,
    )
    if not m:
        return {}
    try:
        return _json.loads(m.group(1))
    except _json.JSONDecodeError:
        return {}


def _agent_vault_launch(ctx, controller, fake_driver, tmp_path, bc_name,
                        *, startup_prompt=None, broker=None, dsn=None):
    """Run a launch under the agent-vault model and stash the result in ctx."""
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    manifest_path = ctx.get("launch_manifest_path")
    if manifest_path is None:
        default_manifest = tmp_path / "bc-manifest.yaml"
        if not default_manifest.exists():
            import yaml as _yaml
            default_manifest.write_text(_yaml.dump({
                "product": "shopsystem product",
                "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
            }))
        manifest_path = default_manifest
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        shopmsg_dsn=dsn,
        startup_prompt=startup_prompt,
        network=None,
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
        agent_vault_broker=broker,
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name
    return result


def _find_bc_base_dockerfile() -> Path | None:
    """Return the tracked Dockerfile that builds shopsystem-bc-base, or None.

    A Dockerfile "builds the shopsystem-bc-base image" if it is a Dockerfile
    whose surrounding context / content identifies it as the bc-base image
    build.  We accept any tracked file named Dockerfile (optionally suffixed)
    whose text references shopsystem-bc-base, ignoring the .git tree.

    A Dockerfile that DERIVES ``FROM`` a shopsystem-bc-base image (e.g. the thin
    docker/bc-lead/Dockerfile added by lead-nsj3) consumes the base image rather
    than building it, and merely mentioning the base in its FROM line must not
    make it masquerade as the bc-base build (bug shopsystem_bc_launcher-hnr).
    Iterate in sorted order so discovery is deterministic regardless of the
    filesystem's rglob ordering.
    """
    for path in sorted(_REPO_ROOT.rglob("Dockerfile*")):
        if ".git" in path.parts:
            continue
        if not path.is_file():
            continue
        text = path.read_text()
        if "shopsystem-bc-base" not in text:
            continue
        if re.search(r"(?im)^\s*FROM\s+\S*shopsystem-bc-base", text):
            continue
        return path
    return None


def _bc_base_dir() -> Path:
    return _REPO_ROOT / "docker" / "bc-base"


def _ca_trust_script_path() -> Path | None:
    """Return the committed CA-trust entrypoint/profile script, or None."""
    candidate = _bc_base_dir() / "agent-vault-ca.sh"
    return candidate if candidate.is_file() else None


def _find_bc_lead_dockerfile() -> Path | None:
    """Return the tracked Dockerfile that builds shopsystem-bc-lead, or None.

    bc-lead is the thin launcher image that DERIVES ``FROM`` a
    shopsystem-bc-base image and adds the docker CLI. We identify it as a tracked
    Dockerfile whose text both references shopsystem-bc-lead AND carries a
    ``FROM ...shopsystem-bc-base`` line (it consumes the base rather than
    building it). Iterate in sorted order for deterministic discovery.
    """
    for path in sorted(_REPO_ROOT.rglob("Dockerfile*")):
        if ".git" in path.parts:
            continue
        if not path.is_file():
            continue
        text = path.read_text()
        if "shopsystem-bc-lead" not in text:
            continue
        if not re.search(r"(?im)^\s*FROM\s+\S*shopsystem-bc-base", text):
            continue
        return path
    return None


_BC_BASE_PINNED_IMAGE = (
    "ghcr.io/dstengle/shopsystem-bc-base:latest"
)


_FABRO_PIN = "v0.254.0"


def _strip_dockerfile_comments(text: str) -> str:
    """Return the Dockerfile text with full-line "# ..." comment lines removed
    (so a commented-out install/pin cannot masquerade as executable)."""
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)
