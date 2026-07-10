"""Support helpers/constants: base_image (extracted from tests/conftest.py).

Plain imported module (NOT a pytest plugin). Domain boundaries are
organizational; step modules import what they reference from here.
"""
from __future__ import annotations

from pathlib import Path
import re
import yaml
from tests.conftest import _REPO_ROOT  # noqa: F401
from tests.support.common import _bc_base_dir, _find_bc_base_dockerfile  # noqa: F401


def _baked_claude_json() -> dict:
    """Parse the FULL ~/.claude.json JSON the bc-base Dockerfile bakes.

    bclaunch-2s6y: the Dockerfile now also seeds ~/.claude.json with the
    onboarding/trust state that skips the first-run wizard (theme ->
    login-method -> folder-trust -> bypass-permissions).  Recover the exact
    object by locating the `> /home/vscode/.claude.json` redirect and parsing
    the single-quoted JSON literal that precedes it.
    """
    import json as _json
    import re as _re

    dockerfile = _find_bc_base_dockerfile()
    text = dockerfile.read_text() if dockerfile else ""
    m = _re.search(
        r"printf\s+'%s\\n'\s+'(\{.*?\})'\s*\\?\s*\n\s*>\s*"
        r"/home/vscode/\.claude\.json\b",
        text,
    )
    if not m:
        return {}
    try:
        return _json.loads(m.group(1))
    except _json.JSONDecodeError:
        return {}


_SHOP_TEMPLATES_LITERAL_PIN_RE = re.compile(
    r"shop-templates @ git\+https://github\.com/dstengle/"
    r"shopsystem-templates(?:\.git)?@v\d+\.\d+\.\d+"
)


_SHOP_TEMPLATES_ARG_PIN_RE = re.compile(
    r"shop-templates @ git\+https://github\.com/dstengle/"
    r"shopsystem-templates(?:\.git)?@\$\{?SHOP_TEMPLATES_VERSION\}?"
)


_SHOP_TEMPLATES_ARG_DEFAULT_SHAPE_RE = re.compile(
    r"ARG\s+SHOP_TEMPLATES_VERSION=v\d+\.\d+\.\d+"
)


def _shop_templates_pinned_by_version_shape(dockerfile_text: str) -> bool:
    """True when shop-templates is pinned by vMAJOR.MINOR.PATCH shape, whether
    as a frozen literal or via the SHOP_TEMPLATES_VERSION build ARG defaulted to
    a vX.Y.Z value (lead-pwa2 parameterization)."""
    if _SHOP_TEMPLATES_LITERAL_PIN_RE.search(dockerfile_text):
        return True
    return bool(
        _SHOP_TEMPLATES_ARG_PIN_RE.search(dockerfile_text)
        and _SHOP_TEMPLATES_ARG_DEFAULT_SHAPE_RE.search(dockerfile_text)
    )


def _workflows_dir() -> Path:
    return _REPO_ROOT / ".github" / "workflows"


def _load_workflows() -> dict[Path, dict]:
    """Load all committed workflow YAML files under .github/workflows."""
    out: dict[Path, dict] = {}
    wf_dir = _workflows_dir()
    if not wf_dir.is_dir():
        return out
    for path in sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml")):
        out[path] = yaml.safe_load(path.read_text())
    return out


_BC_BASE_FRAMEWORK_CLI_PINS = {
    "shopsystem-messaging": ("dstengle", "shopsystem-messaging"),
    "scenarios": ("dstengle", "shopsystem-scenarios"),
    "shop-templates": ("dstengle", "shopsystem-templates"),
    "shopsystem-bc-launcher": ("dstengle", "shopsystem-bc-launcher"),
}


_BC_BASE_BEADS_BINARY_OWNER = "steveyegge"


_BC_BASE_BEADS_BINARY_VERSION = "1.0.3"


def _workflow_text(ctx) -> str:
    path = ctx["publish_workflow"][0]
    return path.read_text()


_AGENT_VAULT_TRUST_VARS = (
    "GIT_SSL_CAINFO",
    "SSL_CERT_FILE",
    "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)


_AGENT_VAULT_CONTAINER_CA_PATH = "/home/vscode/.config/agent-vault/ca.pem"


_AGENT_VAULT_AUTH_VERSION = "0.32.0"


_HEALTHCHECK_BROKER_ENV = "HTTPS_PROXY"


_HEALTHCHECK_DB_ENV = "SHOPMSG_DSN"


def _healthcheck_script_path() -> Path | None:
    """Return the committed bc-base HEALTHCHECK probe script, or None."""
    candidate = _bc_base_dir() / "bc-healthcheck.sh"
    return candidate if candidate.is_file() else None


def _strip_sh_comments(body: str) -> str:
    """Return the probe-script body with whole-line and trailing # comments
    removed, so env-derivation assertions match EXECUTABLE code rather than a
    mention of the env var in a comment. A heredoc-embedded python block uses
    the same '#' comment char, so this is a coarse strip that drops any text
    from an unquoted '#' to end-of-line; that is sufficient because the
    assertions only need the var to appear in a real assignment/expansion, and
    a wrong-target mutation that hard-codes the address would no longer have the
    env var in executable code."""
    out_lines = []
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Drop trailing comments (best-effort; the probe script does not use
        # '#' inside quoted strings on its executable lines).
        hash_idx = line.find("#")
        if hash_idx != -1:
            line = line[:hash_idx]
        out_lines.append(line)
    return "\n".join(out_lines)


def _dockerfile_healthcheck_directive(text: str) -> str | None:
    """Extract the HEALTHCHECK instruction body (including line continuations).

    Returns the full directive text after the HEALTHCHECK keyword (joining
    backslash-continued lines), or None if no HEALTHCHECK instruction is
    present.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s*HEALTHCHECK\b", line):
            collected = [line]
            j = i
            while collected[-1].rstrip().endswith("\\") and j + 1 < len(lines):
                j += 1
                collected.append(lines[j])
            joined = " ".join(c.rstrip().rstrip("\\").strip() for c in collected)
            return re.sub(r"^\s*HEALTHCHECK\s*", "", joined)
    return None


_BOOTSTRAP_FRAMEWORK_CLIS = (
    "shop-templates",
    "shop-msg",
    "bc-container",
    "agent-vault",
)


def _bootstrap_entrypoint_path():
    """Return the committed bootstrap-entrypoint script Path, or None."""
    candidate = _bc_base_dir() / "bootstrap-entrypoint.sh"
    return candidate if candidate.is_file() else None


_BC_BASE_DOCKERFILE_REL = "docker/bc-base/Dockerfile"


_BAKED_DEP_CANONICAL_REPOS = {
    "shop-templates": "dstengle/shopsystem-templates",
    "shop-msg": "dstengle/shopsystem-messaging",
    "scenarios": "dstengle/shopsystem-scenarios",
    "beads": "steveyegge/beads",
}


def _workflow_on(doc) -> dict:
    """The normalized `on:` mapping of a workflow doc (YAML parses bare `on`
    as the boolean True key)."""
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _centralized_poll_workflow():
    """Return (path, doc) for the SINGLE committed workflow that runs the
    bc-base check-bump-rebuild cycle on a recurring schedule, or None.

    Identity (930a6a6579e2a859): triggered by a cron `schedule:` and rebuilds
    the shopsystem-bc-base image (a build step). The bare-dispatch
    rebuild-bc-base.yml (scenario 4e470f7584650a2d) is NOT schedule-triggered,
    so it is excluded — the two coexist without colliding on this identity.
    """
    matches = []
    for path, doc in _load_workflows().items():
        if not isinstance(doc, dict):
            continue
        on = _workflow_on(doc)
        if "schedule" not in on:
            continue
        text = path.read_text()
        if "shopsystem-bc-base" not in text:
            continue
        if not ("build-push-action" in text or "docker build" in text):
            continue
        matches.append((path, doc))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return None
    # More than one schedule-triggered bc-base rebuild workflow violates the
    # "exactly one workflow runs the cycle" invariant; return the list so the
    # Then can assert on the count.
    return matches


def _strip_yaml_comments(text: str) -> str:
    """Return the workflow text with full-line "# ..." comments removed.

    Rationale comments may legitimately NAME the credentials/paths the workflow
    deliberately does NOT use; the forbidden-token scan must inspect the
    EFFECTIVE YAML, not the explanatory prose. Only strips comment-only lines
    (leading-whitespace then "#") to avoid mangling "#" inside quoted values.
    """
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def _find_step(doc, pred):
    jobs = doc.get("jobs", {})
    for job in jobs.values():
        for step in job.get("steps", []) or []:
            if pred(step):
                return step
    return None


_SELF_PIN_DEP_KEY = "shopsystem-bc-launcher"


_SELF_PIN_CANONICAL_REPO = "dstengle/shopsystem-bc-launcher"


def _effective_final_user(dockerfile_text: str) -> str | None:
    """Return the value of the LAST ``USER`` instruction in a Dockerfile, or
    None if the Dockerfile contains no USER instruction.

    Docker applies the most recent USER directive to the runtime container, so
    the final USER instruction is the effective default user (== Config.User ==
    `whoami` for a run with no --user override). A Dockerfile ending USER root
    therefore makes Config.User=root (the lead-t3dy bug); one ending USER vscode
    makes Config.User=vscode (the fix).
    """
    last = None
    for m in re.finditer(r"(?im)^\s*USER\s+(\S+)", dockerfile_text):
        last = m.group(1).strip()
    return last


_UPSTREAM_BASE_VERSION_LABEL = "3.1.2"


def _parse_kv_block(value) -> dict:
    """Parse a GHA action `with:` multiline "key=value" block (labels /
    build-args) into a dict. Accepts the YAML-parsed string (newline-joined)
    or a list of "key=value" strings."""
    out: dict[str, str] = {}
    if value is None:
        return out
    if isinstance(value, str):
        lines = value.splitlines()
    elif isinstance(value, (list, tuple)):
        lines = list(value)
    else:
        return out
    for line in lines:
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def _publish_workflow_doc(ctx):
    """Locate the committed publish workflow triggered on a "v*" tag push."""
    workflows = _load_workflows()
    for path, doc in workflows.items():
        if not isinstance(doc, dict):
            continue
        on = doc.get("on", doc.get(True))
        if not isinstance(on, dict):
            continue
        push = on.get("push")
        if isinstance(push, dict):
            tags = push.get("tags") or []
            if any(str(t).startswith("v") for t in tags):
                return path, doc
    return None


def _build_step_for_image(doc, image_base):
    """Return the docker/build-push-action step in the workflow whose `tags`
    input references the given image_base (e.g.
    "ghcr.io/dstengle/shopsystem-bc-base"), or None."""
    jobs = doc.get("jobs", {})
    for job in jobs.values():
        for step in job.get("steps", []) or []:
            uses = str(step.get("uses", ""))
            if "build-push-action" not in uses:
                continue
            with_ = step.get("with", {}) or {}
            tags = with_.get("tags", "")
            tags_text = tags if isinstance(tags, str) else "\n".join(
                str(t) for t in (tags or [])
            )
            if image_base in tags_text:
                return step
    return None


def _baked_shop_templates_version() -> str | None:
    """Resolve the baked shop-templates version: the
    ARG SHOP_TEMPLATES_VERSION=vX.Y.Z default in the bc-base Dockerfile."""
    dockerfile = _find_bc_base_dockerfile()
    if dockerfile is None:
        return None
    m = re.search(
        r"ARG\s+SHOP_TEMPLATES_VERSION=(v\d+\.\d+\.\d+)", dockerfile.read_text()
    )
    return m.group(1) if m else None


def _bc_base_dockerfile_text() -> str:
    df = _find_bc_base_dockerfile()
    return df.read_text() if df is not None else ""


def _dockerfile_env_value(text: str, name: str) -> str | None:
    """Return the value an `ENV <name>=<value>` instruction sets, or None.

    Matches `ENV NAME=value` (the only form used here). The value may be a
    ${VAR} expansion of a same-named ARG, which is the promote-ARG-to-ENV
    idiom — that still surfaces in `docker inspect`."""
    m = re.search(
        rf"(?im)^\s*ENV\s+{re.escape(name)}=(\S+)", text
    )
    return m.group(1) if m else None


def _dockerfile_arg_declared(text: str, name: str) -> bool:
    return bool(re.search(rf"(?im)^\s*ARG\s+{re.escape(name)}\b", text))


_FABRO_CANONICAL_REPO = "fabro-sh/fabro"


_ANTHROPIC_OAUTH_SHIM_NAME = "anthropic-oauth-shim"


def _committed_oauth_shim_path() -> Path | None:
    """Return the committed anthropic-oauth-shim file the bc-base Dockerfile
    COPYs onto PATH, or None. Discovered by scanning the bc-base Dockerfile's
    build context for the file COPYd as anthropic-oauth-shim."""
    dockerfile = _find_bc_base_dockerfile()
    if dockerfile is None:
        return None
    ctx_dir = dockerfile.parent
    # The Dockerfile COPYs "<src> /usr/local/bin/anthropic-oauth-shim"; find the
    # committed source in the build context.
    dtext = dockerfile.read_text()
    m = re.search(
        r"(?im)^\s*COPY\s+(\S+)\s+\S*/anthropic-oauth-shim\b", dtext
    )
    if m:
        candidate = ctx_dir / m.group(1)
        if candidate.is_file():
            return candidate
    # Fallback: a file literally named anthropic-oauth-shim in the context.
    candidate = ctx_dir / _ANTHROPIC_OAUTH_SHIM_NAME
    if candidate.is_file():
        return candidate
    return None


def _top_level_imported_modules(src: str) -> set[str]:
    """Return the set of top-level module names imported by the python source,
    via AST (robust against import ordering / aliasing)."""
    import ast

    tree = ast.parse(src)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return modules


def _bss3_poll_exec_body(ctx):
    """The centralized poll workflow's executable body with full-line YAML
    comments stripped (5vyb precedent). Logic only in a comment does not count."""
    wf = ctx.get("poll_workflow")
    if wf is None or isinstance(wf, list):
        wf = _centralized_poll_workflow()
        ctx["poll_workflow"] = wf
    assert wf is not None and not isinstance(wf, list), (
        "No single centralized scheduled check-bump-rebuild workflow found."
    )
    ctx.setdefault("poll_workflow_text", wf[0].read_text())
    return _strip_yaml_comments(wf[0].read_text())
