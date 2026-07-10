"""
BDD step definitions for bc-container scenarios.

All Docker interaction is stubbed via FakeDockerDriver — no live daemon required.
All GitHub and git operations in manifest scenarios are stubbed via FakeGitHubDriver
and FakeGitDriver.
"""


from __future__ import annotations


import platform


import re


import subprocess


import sys


import tempfile


from pathlib import Path


import pytest


import yaml


from pytest_bdd import given, parsers, then, when


from bc_launcher.cli import build_parser, main as cli_main


from bc_launcher.controller import BcContainerController


from bc_launcher.driver import ContainerMount


from bc_launcher.manifest import ManifestController, load_manifest, BC_NAME_RE, GITHUB_URL_RE


from tests.fake_driver import (
    FakeDockerDriver,
    FakeRegistryDriver,
    is_bd_bootstrap_command,
    _is_empty_remote_seed_command,
    _is_origin_owner_writeback_command,
    _is_repo_create_command,
)


from tests.fake_github_driver import FakeGitHubDriver


from tests.fake_git_driver import FakeGitDriver


PRODUCT_BCS = [
    {"name": "shopsystem-messaging",     "remote": "https://github.com/dstengle/shopsystem-messaging.git",     "role": "bc"},
    {"name": "shopsystem-scenarios",     "remote": "https://github.com/dstengle/shopsystem-scenarios.git",     "role": "bc"},
    {"name": "shopsystem-templates",     "remote": "https://github.com/dstengle/shopsystem-templates.git",     "role": "bc"},
    {"name": "shopsystem-test-harness",  "remote": "https://github.com/dstengle/shopsystem-test-harness.git",  "role": "bc"},
    {"name": "shopsystem-devcontainer",  "remote": "https://github.com/dstengle/shopsystem-devcontainer.git",  "role": "bc"},
    {"name": "shopsystem-bc-launcher",   "remote": "https://github.com/dstengle/shopsystem-bc-launcher.git",   "role": "bc"},
]


def _make_manifest_content(entries: list[dict]) -> str:
    return yaml.dump({"bcs": entries}, default_flow_style=False, sort_keys=False)


def _write_manifest(path: Path, entries: list[dict]) -> Path:
    path.write_text(_make_manifest_content(entries))
    return path


def _run_manifest_validate(manifest_path: Path, repos_dir: Path | None, github_driver, git_driver=None):
    mc = ManifestController(github_driver=github_driver, git_driver=git_driver)
    result = mc.validate(manifest_path, repos_dir=repos_dir)
    output = "\n".join(result.messages) + "\n"
    return result.ok, output


def _run_manifest_list(manifest_path: Path):
    mc = ManifestController(github_driver=FakeGitHubDriver(), git_driver=FakeGitDriver())
    exit_code, output = mc.list_bcs(manifest_path)
    return exit_code, output


def _run_manifest_sync(manifest_path: Path, repos_dir: Path, git_driver):
    mc = ManifestController(github_driver=FakeGitHubDriver(), git_driver=git_driver)
    exit_code, output = mc.sync(manifest_path, repos_dir)
    return exit_code, output


@pytest.fixture(autouse=True)
def _lead63em_host_state_dir(tmp_path, monkeypatch):
    """Point BCLAUNCHER_HOST_STATE_DIR at a per-test tmp dir (lead-63em).

    Every launch-failure path now persists a diagnostic file under the per-BC
    host state surface (default ``/var/lib/bc-launcher``, which is unwritable
    in CI).  Redirecting it to a per-test tmp dir for the WHOLE suite keeps
    every launch-failure-exercising test (not just the new diagnostic
    scenarios) writing into the sandbox, and prevents env leakage across
    tests.  ``monkeypatch`` restores the prior value automatically at teardown.
    """
    monkeypatch.setenv("BCLAUNCHER_HOST_STATE_DIR", str(tmp_path / "host-state"))


@pytest.fixture
def fake_driver():
    """Return a fresh FakeDockerDriver."""
    return FakeDockerDriver()


@pytest.fixture
def controller(fake_driver):
    """Return a BcContainerController backed by the fake driver.

    lead-cw7m — the controller's bounded readiness-wait scan-dismiss loop
    budgets its total elapsed time against an injectable monotonic clock; the
    fake driver provides a deterministic, strictly-advancing clock so the
    never-clears bounded-timeout path terminates without any real sleeping.
    """
    return BcContainerController(fake_driver, monotonic=fake_driver.monotonic)


@pytest.fixture
def ctx(tmp_path):
    """Shared test context dict with a default credential_home pre-populated."""
    credential_home = tmp_path / "fake_home"
    credential_home.mkdir(parents=True, exist_ok=True)
    (credential_home / ".claude").mkdir(parents=True, exist_ok=True)
    (credential_home / ".config" / "gh").mkdir(parents=True, exist_ok=True)
    gitconfig = credential_home / ".gitconfig"
    if not gitconfig.exists():
        gitconfig.write_text("")
    return {"credential_home": credential_home}


_READINESS_DSN = "postgresql://shopmsg:shopmsg@db.invalid:5432/shopsystem"


@pytest.fixture
def fake_github():
    return FakeGitHubDriver()


@pytest.fixture
def fake_git():
    return FakeGitDriver()


_J351_SLOW_PROMPT = "J351_SLOW_BROKERED_BOOT_PROMPT"


def _prompt_submit_send_keys(fake_driver, container_name, prompt):
    """Return the send-keys invocations attributable to the prompt submission.

    The launch path issues earlier readiness send-keys (launching claude,
    accepting the trust prompt) that are NOT part of the --startup-prompt
    handling.  The prompt-submit handling is the trailing pair: the
    text-carrying invocation for ``prompt`` and the Enter invocation that
    immediately follows it.  We locate the invocation carrying the prompt
    text as a discrete token and return it together with the next send-keys
    invocation.
    """
    calls = fake_driver.send_keys_calls(container_name)
    # Index of the (last) invocation whose payload carries the prompt text as
    # a discrete token.
    text_idx = None
    for i, c in enumerate(calls):
        if prompt in c.command:
            text_idx = i
    assert text_idx is not None, (
        f"No tmux send-keys invocation carried prompt text {prompt!r} in "
        f"{container_name!r}; recorded: {[c.command for c in calls]!r}"
    )
    assert text_idx + 1 < len(calls), (
        f"Expected a send-keys invocation AFTER the prompt-text invocation "
        f"(the discrete Enter), but the prompt-text invocation was the last "
        f"send-keys recorded: {[c.command for c in calls]!r}"
    )
    return calls[text_idx], calls[text_idx + 1], text_idx, text_idx + 1


from bc_launcher.controller import (
    AGENT_VAULT_MITM_PROXY_PORT,
    AGENT_VAULT_PLACEHOLDER_TOKEN,
    CONTAINER_CLAUDE_CREDENTIALS_PATH,
    DEFAULT_AGENT_VAULT_BROKER,
)


_REAL_OAUTH_TOKEN = "sk-ant-REAL-oauth-accessToken-DO-NOT-LEAK"


_REAL_GITHUB_TOKEN = "ghp_REAL_github_token_DO_NOT_LEAK"


_UNREACHABLE_BROKER = "http://no-such-agent-vault.invalid:9999"


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


def _baked_placeholder_credentials() -> dict:
    """Back-compat shim: the baked .credentials.json as a dict.

    Returns the FULL nested credential object (bclaunch-2s6y).  Callers that
    previously read a top-level ``accessToken`` are updated to read the nested
    ``claudeAiOauth.accessToken`` path; this remains the single parse point.
    """
    return _baked_credentials_json()


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


from bc_launcher.controller import (
    AGENT_VAULT_ADDR_ENV,
    AGENT_VAULT_TOKEN_ENV,
    AGENT_VAULT_VAULT_ENV,
    CONTAINER_BROKER_CA_PATH,
)


def _clone_exec_env(ctx, fake_driver) -> dict:
    call = fake_driver.clone_exec_call(ctx["container_name"])
    assert call is not None, (
        "Expected a launch-time `git clone` exec call to have been recorded; "
        "none found."
    )
    return call.env or {}


def _runtime_proxy(ctx, fake_driver) -> str:
    """The HTTPS_PROXY value the container was actually launched with."""
    return fake_driver.container_proxy_env(ctx["container_name"])


_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"


_FAKE_BROKER_CA_PEM = (
    "-----BEGIN CERTIFICATE-----\nFAKEBROKERCAFORTESTS\n"
    "-----END CERTIFICATE-----\n"
)


_REPO_ROOT = Path(__file__).resolve().parent.parent


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


_BC_BASE_LATEST_REF = "ghcr.io/dstengle/shopsystem-bc-base:latest"


def _digest_sha_for_label(label):
    """Turn a scenario digest LABEL (e.g. "D_old"/"D_new") into a distinct,
    well-formed sha256 value carrying the label, so D_old and D_new are
    genuinely DIFFERENT content-addressable digests (D_old != D_new)."""
    label_hex = "".join(c for c in label.lower() if c in "0123456789abcdef")
    # Prefix with the label's letters mapped to hex so labels with no hex
    # chars of their own (e.g. "D_old" -> "d") still differ from one another.
    seed = "".join(format(ord(c), "x") for c in label.lower())
    return f"sha256:{(seed + label_hex)}".ljust(71, "0")[:71]


_BC_IMAGE_ENV = "BC_IMAGE"


def _run_image_launch(bc_name, ctx, fake_driver, controller, tmp_path, image):
    """Drive a launch and record the started container's docker run command.

    A fresh, manifest-backed launch through the FakeDockerDriver so the
    resolved launch image is observable as the trailing image token of the
    recorded docker run command for the container.
    """
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    default_manifest = tmp_path / "bc-manifest.yaml"
    if not default_manifest.exists():
        import yaml as _yaml
        default_manifest.write_text(_yaml.dump({
            "product": "shopsystem product",
            "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
        }))
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        image=image,
        manifest_path=default_manifest,
        credential_home=ctx.get("credential_home"),
    )
    ctx["result"] = result
    ctx["fake_driver_for_run"] = fake_driver
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


_AGENT_VAULT_TRUST_VARS = (
    "GIT_SSL_CAINFO",
    "SSL_CERT_FILE",
    "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)


_AGENT_VAULT_CONTAINER_CA_PATH = "/home/vscode/.config/agent-vault/ca.pem"


def _bc_base_dir() -> Path:
    return _REPO_ROOT / "docker" / "bc-base"


def _ca_trust_script_path() -> Path | None:
    """Return the committed CA-trust entrypoint/profile script, or None."""
    candidate = _bc_base_dir() / "agent-vault-ca.sh"
    return candidate if candidate.is_file() else None


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


_MULTILINE_BROKER_CA_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIB3TCCAYOgAwIBAgIUFAKEBROKERCAFORTESTSLEADB14A0123456789ABCw\n"
    "RAYDVQQDDD1hZ2VudC12YXVsdC1icm9rZXItY2EtbXVsdGlsaW5lLXBlbS1sZWFk\n"
    "LWIxNGEtdGVzdC1jZXJ0aWZpY2F0ZS1ib2R5LWxpbmUtdGhyZWUtaGVyZXdpdGgw\n"
    "HhcNMjYwNjE5MDAwMDAwWhcNMzYwNjE2MDAwMDAwWjA8MTowOAYDVQQDDDFmYWtl\n"
    "-----END CERTIFICATE-----\n"
)


def _legacy_only_empty_remote_classifier(message: str) -> bool:
    """Reference LEGACY-ONLY empty-remote classifier (lead-ypnz negative
    control).

    This is the pre-GAP-D predicate: it matches ONLY the legacy "git remote
    has no branches" text.  It deliberately does NOT know the current bc-base
    dolt "contains no Dolt data" text, so the scenario can assert that the
    seed firing on the current-dolt error is CAUSED by the version-robust
    match — a legacy-only classifier would leave the seed unfired rather than
    retrying unconditionally.
    """
    text = message.lower()
    return "git remote has no branches" in text or (
        "no branches" in text and "initialize" in text
    )


def _resolve_standup_tracker_slug(tracker: str, bc_name: str) -> str:
    """Resolve the scenario's abstract "<owner>/<bc>-beads" tracker template
    into the CONCRETE GitHub slug the standup flow must target.

    lead-jq9b / ADR-043 D5.  The scenario pins the tracker NAME form via the
    `{tracker}` placeholder; the load-bearing binding is that the standup's
    provisioning commands target exactly this concrete slug.  `<owner>` binds
    to the beads remote org the controller derives its slug under, and `<bc>`
    binds to the BC being stood up.  Resolving from the scenario text (not a
    hardcoded slug) is what ties the on-disk contract to the emitted command:
    change the pinned NAME form and this expectation changes with it.
    """
    from bc_launcher.controller import BEADS_REMOTE_ORG
    return tracker.replace("<owner>", BEADS_REMOTE_ORG).replace("<bc>", bc_name)


def _zxtk_default_manifest(ctx, tmp_path, bc_name="shopsystem-messaging"):
    """Write a default manifest so network resolution succeeds for a launch
    that does not otherwise configure one (lead-zxtk scenarios)."""
    manifest_path = tmp_path / "bc-manifest.yaml"
    if not manifest_path.exists():
        import yaml as _yaml
        manifest_path.write_text(_yaml.dump({
            "product": "shopsystem product",
            "bcs": [{
                "name": bc_name,
                "remote": f"https://github.com/shopsystem/{bc_name}.git",
                "role": "bc",
            }],
        }))
    return manifest_path


_WDVX_DOCKER_FAULTS = {
    "the socket is permission-denied to the calling user":
        "permission_denied",
    "the socket is not mounted into the calling environment":
        "not_mounted",
}


_ESCAPABLE_OPTION_SCREEN = (
    "Select an option for your session:\n"
    "  > Use the default theme\n"
    "    Pick a different theme\n"
    "(press esc to dismiss and keep current settings)\n"
)


_UNESCAPABLE_OPTION_SCREEN = (
    "Select an option to continue:\n"
    "  > Accept the license agreement\n"
    "    Decline\n"
    "(you must choose one of the options above to proceed)\n"
)


def _escape_send_keys(fake_driver, container_name, session):
    """Send-keys invocations whose SOLE payload is the Escape key."""
    out = []
    for c in fake_driver.send_keys_calls(container_name):
        cmd = c.command
        if cmd[:4] == ["tmux", "send-keys", "-t", session] and cmd[4:] == ["Escape"]:
            out.append(c)
    return out


_READINESS_GENERIC_PROMPT = (
    "Set up your editor integration?\n"
    "  1. Yes\n"
    "  2. Not now\n"
    "(Esc to cancel)\n"
)


_READINESS_FULLSCREEN_PROMPT = (
    "Try the new fullscreen renderer?\n"
    "  1. Yes\n"
    "  2. Not now, Esc to cancel\n"
)


_READINESS_STARTUP_PROMPT = "bd prime"


def _launch_with_readiness_prompt(
    ctx, fake_driver, controller, tmp_path, content, *, clears_on_escape
):
    """Configure a readiness-wait blocking prompt and run launch.

    Both readiness barriers (messaging DB, agent-vault broker) pass; claude
    starts and the PRE-trust CLAUDE_READY_MARKER is observed; the blocking
    prompt then prevents the POST-trust input-ready marker from appearing
    until an Escape dismisses it (when ``clears_on_escape``).
    """
    bc_name = "shopsystem-messaging"
    container_name = f"bc-{bc_name}"
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    dsn = _READINESS_DSN
    fake_driver.set_dsn_reachable(dsn, reachable=True)
    fake_driver.simulate_readiness_wait_prompt(
        container_name, content, clears_on_escape=clears_on_escape
    )
    manifest_path = tmp_path / "bc-manifest.yaml"
    if not manifest_path.exists():
        manifest_path.write_text(yaml.dump({
            "product": "shopsystem product",
            "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
        }))
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        shopmsg_dsn=dsn,
        startup_prompt=_READINESS_STARTUP_PROMPT,
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
    )
    ctx["result"] = result
    ctx["container_name"] = container_name
    ctx["bc_name"] = bc_name
    ctx["startup_prompt"] = _READINESS_STARTUP_PROMPT


_LEAD_63EM_FAULT_TO_MARKER = {
    "the messaging database at SHOPMSG_DSN is unreachable": "messaging-db",
    "the agent-vault broker on the shopsystem network is unreachable": "agent-vault",
    "the readiness barrier never reports both supporting servers ready": "readiness",
    "claude or its tmux session never started inside the container": "agent-startup",
}


def _lead63em_point_state_dir_at_sandbox(ctx):
    """Record the per-test host state surface dir from the autouse fixture.

    The ``_lead63em_host_state_dir`` autouse fixture has already pointed
    BCLAUNCHER_HOST_STATE_DIR at a per-test tmp dir; capture it in ctx so the
    Then steps can assert the diagnostic file lands under that surface.
    """
    import os as _os
    ctx["host_state_dir"] = _os.environ["BCLAUNCHER_HOST_STATE_DIR"]


def _lead63em_read_diagnostic_from_host(ctx):
    """Read the persisted diagnostic file from the HOST.

    Reads the documented per-BC host path directly off the host filesystem —
    NO docker exec, NO tmux attach, and WITHOUT touching the launch result's
    stderr.  Returns the file's text.  Asserts the file exists.
    """
    from bc_launcher.controller import launch_diagnostic_path
    bc_name = ctx["bc_name"]
    path = launch_diagnostic_path(bc_name)
    assert path.exists(), (
        f"Expected a persisted launch-diagnostic file at the documented "
        f"per-BC host location {path}, but it does not exist"
    )
    ctx["diagnostic_path"] = path
    return path.read_text(encoding="utf-8")


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


_SELF_ADVANCE_STARTUP_PROMPT = "bd prime"


def _launch_with_self_advance_mode(ctx, fake_driver, controller, tmp_path, mode):
    """Configure a self-advance readiness mode and run launch (lead-gw9v).

    Both readiness barriers (messaging DB, agent-vault broker) pass; the
    workspace-trust gate during the initial readiness wait is resolved per
    ``mode`` (see fake_driver.simulate_self_advance_readiness).
    """
    bc_name = "shopsystem-messaging"
    container_name = f"bc-{bc_name}"
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    dsn = _READINESS_DSN
    fake_driver.set_dsn_reachable(dsn, reachable=True)
    fake_driver.simulate_self_advance_readiness(container_name, mode)
    manifest_path = tmp_path / "bc-manifest.yaml"
    if not manifest_path.exists():
        manifest_path.write_text(yaml.dump({
            "product": "shopsystem product",
            "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
        }))
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        shopmsg_dsn=dsn,
        startup_prompt=_SELF_ADVANCE_STARTUP_PROMPT,
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
    )
    ctx["result"] = result
    ctx["container_name"] = container_name
    ctx["bc_name"] = bc_name
    ctx["startup_prompt"] = _SELF_ADVANCE_STARTUP_PROMPT


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


def _strip_dockerfile_comments(text: str) -> str:
    """Return the Dockerfile text with whole-line ``#`` comments removed.

    Image-content scenarios must bind to actual build INSTRUCTIONS, not to
    documentation prose: a Dockerfile that merely mentions a package in a
    comment but never installs it must still fail the teeth. We drop lines whose
    first non-whitespace character is ``#`` (Dockerfile comments are
    whole-line) so the detectors below see only executable instructions.
    """
    return "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )


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


_BC_BASE_PINNED_IMAGE = (
    "ghcr.io/dstengle/shopsystem-bc-base:latest"
)


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


_FABRO_PIN = "v0.254.0"


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


def _strip_dockerfile_comments(text: str) -> str:
    """Return the Dockerfile text with full-line "# ..." comment lines removed
    (so a commented-out install/pin cannot masquerade as executable)."""
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


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


import hashlib as _ky63_hashlib


import json as _ky63_json


import os as _ky63_os


import shutil as _ky63_shutil


import tarfile as _ky63_tarfile


import urllib.request as _ky63_urlreq


from bc_launcher.controller import (  # noqa: E402
    _fabro_def_asset_root as _ky63_def_asset_root,
    _load_fabro_def_files as _ky63_load_def_files,
)


_KY63_FABRO_VERSION = "v0.254.0"


_KY63_FABRO_TRIPLES = {
    "x86_64": "x86_64-unknown-linux-gnu",
    "aarch64": "aarch64-unknown-linux-gnu",
    "arm64": "aarch64-unknown-linux-gnu",
}


def _ky63_cache_dir() -> Path:
    base = _ky63_os.environ.get("FABRO_CACHE_DIR") or "/tmp/fabro-cache"
    d = Path(base)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ky63_locate_or_fetch_fabro() -> tuple[str | None, str]:
    """Return (path_to_fabro_binary, note).  Prefer an on-PATH / cached fabro;
    otherwise DOWNLOAD the correct target-triple release asset (network via
    HTTPS_PROXY).  Returns (None, reason) ONLY if the binary genuinely cannot
    be obtained (no network) — the caller then SKIPs honestly."""
    on_path = _ky63_shutil.which("fabro")
    if on_path:
        return on_path, f"fabro resolved on PATH at {on_path}"

    cache = _ky63_cache_dir()
    cached = cache / "fabro"
    if cached.is_file() and _ky63_os.access(cached, _ky63_os.X_OK):
        return str(cached), f"fabro resolved from cache at {cached}"

    machine = platform.machine().lower()
    triple = _KY63_FABRO_TRIPLES.get(machine)
    if triple is None:
        return None, f"no known fabro target-triple for arch {machine!r}"

    asset = f"fabro-{triple}.tar.gz"
    url = (
        f"https://github.com/fabro-sh/fabro/releases/download/"
        f"{_KY63_FABRO_VERSION}/{asset}"
    )
    tarball = cache / asset
    try:
        proxy = (
            _ky63_os.environ.get("HTTPS_PROXY")
            or _ky63_os.environ.get("https_proxy")
        )
        if proxy:
            opener = _ky63_urlreq.build_opener(
                _ky63_urlreq.ProxyHandler({"https": proxy, "http": proxy})
            )
        else:
            opener = _ky63_urlreq.build_opener()
        with opener.open(url, timeout=120) as resp, tarball.open("wb") as fh:
            _ky63_shutil.copyfileobj(resp, fh)
        with _ky63_tarfile.open(tarball, "r:gz") as tf:
            # --strip-components=1 equivalent: pull the 'fabro' member to cache root
            member = None
            for m in tf.getmembers():
                if m.isfile() and Path(m.name).name == "fabro":
                    member = m
                    break
            if member is None:
                return None, f"fabro binary not found inside {asset}"
            member.name = "fabro"
            tf.extract(member, path=cache)
        cached.chmod(0o755)
        return str(cached), f"fabro downloaded from {url}"
    except Exception as exc:  # pragma: no cover - network-dependent
        return None, f"fabro binary could not be obtained (no network?): {exc!r}"


def _ky63_materialize_def(dest: Path) -> Path:
    """Lay out the committed def bundle bytes under ``dest`` exactly as the
    launcher would place them at /workspace/.fabro/ (runnable FROM THE DEF
    ALONE), and return the workflow.fabro path.  Binds to the REAL committed
    asset bytes via the launcher's own loader."""
    files = _ky63_load_def_files()
    for rel, data in files.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return dest / "workflow.fabro"


_KY63_REAL_NODES = {
    "start", "done", "reported", "halt", "prime", "health", "arm", "armed",
    "classify", "suff", "worktree", "plan", "impl", "redgate", "integ",
    "review", "wdg_r", "emit_r", "impl_f", "wdg_f", "emit_f", "emit_clar",
    "emit_blk",
}


_KY63_FAILSAFE_SINKS = {"halt", "emit_blk"}


_KY63_TERMINALS = {"start", "done", "reported", "halt"}


def _ky63_strip_line_comments(s: str) -> str:
    """Strip // line-comments, quote-aware (a // inside a "..." string, e.g. a
    URL, is NOT a comment)."""
    out = []
    for line in s.splitlines():
        res = []
        i = 0
        inq = False
        while i < len(line):
            c = line[i]
            if c == '"':
                inq = not inq
                res.append(c)
                i += 1
                continue
            if not inq and c == "/" and i + 1 < len(line) and line[i + 1] == "/":
                break
            res.append(c)
            i += 1
        out.append("".join(res))
    return "\n".join(out)


def _ky63_parse_nodes(graph: str) -> dict[str, str]:
    """Return {node_name: attr_body} for each ``name [ ... ]`` definition,
    scanning the matching ] quote-aware so a shell ``[ -z ... ]`` / ``[0-9]``
    inside a script= string does not close the node early.  Excludes the
    reserved ``graph [ ... ]`` attribute statement."""
    nodes: dict[str, str] = {}
    for m in re.finditer(r"(?m)^\s*([A-Za-z_]\w*)\s*\[", graph):
        name = m.group(1)
        if name == "graph":
            continue
        i = m.end() - 1
        depth = 0
        inq = False
        j = i
        while j < len(graph):
            c = graph[j]
            if inq:
                if c == "\\":
                    j += 2
                    continue
                if c == '"':
                    inq = False
            else:
                if c == '"':
                    inq = True
                elif c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                    if depth == 0:
                        nodes.setdefault(name, graph[i + 1:j])
                        break
            j += 1
    return nodes


def _ky63_parse_edges(graph: str) -> list[tuple[str, str, str]]:
    """Return [(src, dst, attr_block)] for each real-node edge.  Comments are
    stripped; edge attribute strings (e.g. condition="outcome=failed") are
    preserved.  Prose arrows inside node bodies are excluded by filtering to
    known real-node endpoints."""
    lc = _ky63_strip_line_comments(graph)
    inner = lc[lc.index("{") + 1:lc.rindex("}")]
    edges: list[tuple[str, str, str]] = []
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)", inner):
        src, dst = m.group(1), m.group(2)
        k = m.end()
        while k < len(inner) and inner[k] in " \t\n":
            k += 1
        attr = ""
        if k < len(inner) and inner[k] == "[":
            depth = 0
            inq = False
            j = k
            while j < len(inner):
                c = inner[j]
                if inq:
                    if c == "\\":
                        j += 2
                        continue
                    if c == '"':
                        inq = False
                else:
                    if c == '"':
                        inq = True
                    elif c == "[":
                        depth += 1
                    elif c == "]":
                        depth -= 1
                        if depth == 0:
                            attr = inner[k:j + 1]
                            break
                j += 1
        if src in _KY63_REAL_NODES and dst in _KY63_REAL_NODES:
            edges.append((src, dst, attr))
    return edges


def _ky63_success_reach(edges, start_node):
    """Nodes reachable from ``start_node`` following ONLY non-failsafe
    (success-advance) edges."""
    succ: dict[str, list[str]] = {}
    for s, d, a in edges:
        if "outcome=failed" in a:
            continue
        succ.setdefault(s, []).append(d)
    seen: set[str] = set()
    stack = [start_node]
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        for d in succ.get(x, []):
            stack.append(d)
    return seen


def _ky63_complete_emitters(nodes: dict[str, str]) -> list[str]:
    """Node names whose body emits a GATED work_done(complete) via bc-emit."""
    return [
        n for n, b in nodes.items()
        if "bc-emit" in b and "work-done" in b and "--status complete" in b
    ]


import base64 as _vwib_base64


import json as _vwib_json


import socket as _vwib_socket


import subprocess as _vwib_subprocess


import sys as _vwib_sys


import time as _vwib_time


import tomllib as _vwib_tomllib


from bc_launcher.controller import (
    ANTHROPIC_OAUTH_SHIM_BIN as _VWIB_SHIM_BIN,
    FABRO_ANTHROPIC_ADAPTER as _VWIB_ADAPTER,
    FABRO_ANTHROPIC_BASE_URL as _VWIB_BASE_URL,
    FABRO_SETTINGS_CONTAINER_PATH as _VWIB_SETTINGS_PATH,
    FABRO_SHIM_HOST as _VWIB_SHIM_HOST,
    FABRO_SHIM_PORT as _VWIB_SHIM_PORT,
    _fabro_def_asset_root as _vwib_def_asset_root,
    _fabro_shim_start_argv as _vwib_shim_start_argv,
)


_VWIB_REPO_ROOT = Path(__file__).resolve().parent.parent


_VWIB_COMMITTED_SHIM = _VWIB_REPO_ROOT / "docker" / "bc-base" / "anthropic-oauth-shim"


def _vwib_fabro_launch_exec_calls(ctx):
    """The recorded exec_calls from the fabro-path launch under test."""
    return ctx["fabro_launch_driver"].exec_calls


def _vwib_shim_start_call(ctx):
    """Locate the launcher exec that STARTS the baked shim on the fabro path.

    Matches the `/bin/sh -c` script that invokes the shim binary with its serve
    args. Returns the ExecCall or None.
    """
    for c in _vwib_fabro_launch_exec_calls(ctx):
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and _VWIB_SHIM_BIN in c.command[2]
            and f"--port {_VWIB_SHIM_PORT}" in c.command[2]
        ):
            return c
    return None


def _vwib_settings_write_call(ctx):
    """Locate the launcher exec that WRITES fabro's effective settings.toml."""
    for c in _vwib_fabro_launch_exec_calls(ctx):
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and _VWIB_SETTINGS_PATH in c.command[2]
            and "base64 -d" in c.command[2]
        ):
            return c
    return None


def _vwib_recover_written_settings(ctx) -> str:
    """Recover the settings.toml bytes the launcher WROTE, byte-verbatim.

    The launcher's settings-write exec base64-encodes the file bytes on the
    host and base64-decodes them in the container. We recover the exact bytes
    the launcher wrote by extracting the base64 payload from the recorded
    script (binding to the launcher's REAL output, not a re-derivation).
    """
    call = _vwib_settings_write_call(ctx)
    assert call is not None, (
        "The fabro-path launcher did not emit a settings.toml write exec "
        f"targeting {_VWIB_SETTINGS_PATH}. exec_calls: "
        f"{[c.command[:3] for c in _vwib_fabro_launch_exec_calls(ctx)]!r}"
    )
    script = call.command[2]
    # shlex.quote leaves a pure-base64 token UNQUOTED (no shell-special chars),
    # so tolerate an optional surrounding single-quote.
    m = re.search(
        r"printf %s '?([A-Za-z0-9+/=]+)'? \| base64 -d", script
    )
    assert m, f"could not recover base64 settings payload from script: {script!r}"
    return _vwib_base64.b64decode(m.group(1)).decode("utf-8")


from bc_launcher.cli import build_parser as _cadr_build_parser


from bc_launcher.controller import (
    AGENT_TMUX_SESSION as _CADR_AGENT_SESSION,
    LAUNCH_PATH_FABRO as _CADR_LAUNCH_PATH_FABRO,
    LAUNCH_PATH_TMUX as _CADR_LAUNCH_PATH_TMUX,
    _fabro_server_start_argv as _cadr_server_start_argv,
)


def _cadr_write_manifest(tmp_path, bc_name):
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text(
        "product: shopsystem product\n"
        "bcs:\n"
        f"  - name: {bc_name}\n"
        f"    remote: https://github.com/shopsystem/{bc_name}.git\n"
        "    role: bc\n"
    )
    return manifest_path


def _cadr_exec_calls(ctx):
    """The recorded exec_calls from the launch under test."""
    return ctx["cadr_driver"].exec_calls


def _cadr_fabro_engage_call(ctx):
    """Locate the launcher exec that drives the fabro ENGAGE (server start +
    run) on the fabro path. Matches the `/bin/sh -c` script that carries the
    `fabro server start` argv. Returns the ExecCall or None."""
    for c in _cadr_exec_calls(ctx):
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and "fabro server start" in c.command[2]
        ):
            return c
    return None


def _cadr_fabro_run_calls(ctx):
    """All launcher execs whose script issues a `fabro run` (should be present
    on the fabro path, absent on the tmux-default path)."""
    return [
        c
        for c in _cadr_exec_calls(ctx)
        if c.command[:2] == ["/bin/sh", "-c"]
        and len(c.command) >= 3
        and "fabro run" in c.command[2]
    ]


def _cadr_fabro_server_calls(ctx):
    """All launcher execs whose script issues a `fabro server start`."""
    return [
        c
        for c in _cadr_exec_calls(ctx)
        if c.command[:2] == ["/bin/sh", "-c"]
        and len(c.command) >= 3
        and "fabro server start" in c.command[2]
    ]


def _cadr_tmux_agent_send_keys(ctx):
    """All tmux send-keys execs targeting the `agent` session (the tmux engage
    tier). Bound to the launcher's actual recorded send-keys calls."""
    return [
        c
        for c in _cadr_exec_calls(ctx)
        if c.command[:2] == ["tmux", "send-keys"]
        and "-t" in c.command
        and _CADR_AGENT_SESSION
        in c.command[c.command.index("-t") + 1: c.command.index("-t") + 2]
    ]


def _cadr_claude_engage_send_keys(ctx):
    """The tmux send-keys execs that START claude (`agent-vault run -- claude`)
    — the claude engage. Present on the tmux path, absent on the fabro path."""
    return [
        c
        for c in _cadr_tmux_agent_send_keys(ctx)
        if any("claude" in tok for tok in c.command)
    ]


def _cadr_launch_help_text():
    parser = _cadr_build_parser()
    for action in parser._subparsers._group_actions:
        choices = getattr(action, "choices", None)
        if choices and "launch" in choices:
            return choices["launch"].format_help()
    raise AssertionError("could not resolve the launch subparser help")


from bc_launcher.controller import (  # noqa: E402
    FABRO_SERVER_SETTINGS_CONTAINER_PATH as _ODD9_SERVER_SETTINGS_PATH,
    FABRO_SETTINGS_CONTAINER_PATH as _ODD9_PROJECT_SETTINGS_PATH,
    FABRO_DEF_CONTAINER_DIR as _ODD9_DEF_DIR,
)


_ODD9_BC = "shopsystem-messaging"


def _odd9_drive_fabro_launch(bc_name, ctx, fake_driver, controller, tmp_path,
                             work_id=None, extra_argv=None):
    """Drive the REAL launcher on the --orchestrator fabro path, resolving the
    launch_path exactly as the CLI does.  When ``work_id`` is None NO --work-id
    is supplied (the ADR-058 D6 interface: the fabro path takes no work id).
    Records the SAME ctx keys the lead-cadr helpers read so their assertions and
    the shared readiness/inspect/parity steps work unchanged."""
    argv = ["launch", bc_name, "--orchestrator", "fabro"]
    if work_id is not None:
        argv += ["--work-id", work_id]
    if extra_argv:
        argv += extra_argv
    parser = _cadr_build_parser()
    args = parser.parse_args(argv)
    assert args.orchestrator == "fabro"
    launch_path = (
        _CADR_LAUNCH_PATH_FABRO
        if (args.orchestrator == "fabro" or getattr(args, "fabro_path", False))
        else _CADR_LAUNCH_PATH_TMUX
    )
    assert launch_path == _CADR_LAUNCH_PATH_FABRO
    manifest_path = _cadr_write_manifest(tmp_path, bc_name)
    result = controller.launch(
        bc_name=bc_name,
        repo_url=f"https://github.com/shopsystem/{bc_name}.git",
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
        launch_path=launch_path,
        work_id=getattr(args, "work_id", None),
    )
    assert result.exit_code == 0, (
        f"fabro-path launch failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    ctx["cadr_result"] = result
    ctx["cadr_driver"] = fake_driver
    ctx["cadr_bc_name"] = bc_name
    ctx["container_name"] = f"bc-{bc_name}"
    return result


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


def _raw_git_scheme_aborts(url: str) -> bool:
    """Reference model (lead-ktl0 / GAP E): raw git rejects a `git+https://`
    scheme with the remote-helper-aborted fatal (exit 128).  The `git+https`
    transport is a Dolt-tooling convention; passed to a RAW `git push`/
    `git ls-remote` it errors "git: 'remote-git+https' is not a git command;
    fatal: remote helper 'git+https' aborted session".  A plain `https://`
    URL is accepted by raw git."""
    return url.startswith("git+")


def _model_seed_outcome(git_push_url: str) -> dict:
    """Reference model of the seed script under `set -e`, keyed on the URL the
    git-side `git push` targets (lead-ktl0 / GAP E).

    A `git+https://` URL is the PRE-FIX state: raw git rejects the scheme
    (remote-helper-aborted, exit 128) and the push is FATAL, so under `set -e`
    the seed aborts BEFORE `bd dolt push` and the tracker is never dolt-seeded.
    A plain `https://` URL is the POST-FIX state: the git-side push is a
    redundant non-fatal noop (create-absent already `--add-readme`'d the
    initial branch), so the seed reaches and runs `bd dolt push`, which seeds
    refs/dolt/* and lets the retried `bd bootstrap` exit zero.
    """
    if _raw_git_scheme_aborts(git_push_url):
        return {
            "git_push_result": "remote-helper-aborted-exit-128",
            "reaches_dolt_push": "never-reached",
            "dolt_refs_seeded": "absent",
            "bootstrap_exit": "nonzero",
        }
    return {
        "git_push_result": "redundant-noop-non-fatal",
        "reaches_dolt_push": "reached-and-run",
        "dolt_refs_seeded": "present",
        "bootstrap_exit": "zero",
    }


def _parse_seed_git_side_push(script: str):
    """Structurally extract the git-side push facts from the executable
    `_empty_remote_seed_script` string (lead-ktl0 / GAP E):

      (url, non_fatal, before_dolt_push, ls_remote_url)

    - `url`             : the URL the raw `git -C "$tmp" push "<url>" main`
                          statement targets.
    - `non_fatal`       : whether that push statement is non-fatal
                          (`... || true`), so `set -e` does not abort on it.
    - `before_dolt_push`: whether the git-side push is ordered BEFORE the
                          `bd dolt push` seed step.
    - `ls_remote_url`   : the URL the raw `git ls-remote <url>` verify tail
                          targets (also a raw-git op that would abort on a
                          `git+https://` scheme).
    """
    push_m = re.search(r'git -C "\$tmp" push "([^"]+)" main', script)
    assert push_m, f"seed script has no git-side `git push` statement: {script!r}"
    url = push_m.group(1)
    # The push statement runs up to its `;` terminator; `|| true` inside it is
    # what makes the push non-fatal under `set -e`.
    push_stmt = script[push_m.start():].split(";", 1)[0]
    non_fatal = "|| true" in push_stmt
    dolt_push_idx = script.find("bd dolt push")
    assert dolt_push_idx != -1, f"seed script has no `bd dolt push` step: {script!r}"
    before_dolt_push = push_m.start() < dolt_push_idx
    ls_m = re.search(r"git ls-remote (\S+) 'refs/dolt/\*'", script)
    ls_remote_url = ls_m.group(1) if ls_m else None
    return url, non_fatal, before_dolt_push, ls_remote_url


def _parse_seed_create_fresh(script: str):
    """Structurally extract the create-fresh-from-metadata.json facts from the
    executable `_empty_remote_seed_script` string (lead-vb6j / ROOT / GAP G):

      (metadata_ref, create_fresh_idx, adopts_committed_prefix,
       before_remote_add, before_dolt_push)

    - `metadata_ref`           : whether the seed reads the committed
                                 `.beads/metadata.json` as the create-fresh
                                 prefix source.
    - `create_fresh_idx`       : index of the create-fresh command (`bd init`,
                                 which CREATES a fresh prefixed local dolt DB —
                                 distinct from `bd bootstrap`, which CLONES the
                                 configured remote and hard-fails on an empty
                                 one).
    - `adopts_committed_prefix`: whether the create-fresh adopts the COMMITTED
                                 issue_prefix (a shell var populated FROM
                                 `.beads/metadata.json`) rather than a
                                 hard-coded BC-name-derived literal.
    - `before_remote_add`      : whether the create-fresh is ordered BEFORE the
                                 `bd dolt remote add origin` step (so the local
                                 DB is create-fresh'd with the dolt remote NOT
                                 yet configured).
    - `before_dolt_push`       : whether the create-fresh is ordered BEFORE the
                                 `bd dolt push` seed step (so the seed pushes a
                                 PREFIXED database).
    """
    metadata_ref = ".beads/metadata.json" in script
    # The create-fresh primitive: `bd init` creates a fresh prefixed local dolt
    # DB (`bd init [-p <prefix>]`).  It does NOT clone the configured remote —
    # unlike `bd bootstrap`, which on an empty remote hard-fails "contains no
    # Dolt data".
    cf_m = re.search(r"bd init\b[^;]*", script)
    create_fresh_idx = cf_m.start() if cf_m else -1
    remote_add_idx = script.find("bd dolt remote add")
    dolt_push_idx = script.find("bd dolt push")
    # Adopts the COMMITTED prefix: the create-fresh passes `-p "$<var>"` where
    # the var is populated from `.beads/metadata.json` — NOT a name-derived
    # literal.
    adopts_committed_prefix = bool(
        cf_m
        and re.search(r'bd init\b[^;]*-p\s+"\$', cf_m.group(0))
        and metadata_ref
    )
    before_remote_add = (
        create_fresh_idx != -1
        and remote_add_idx != -1
        and create_fresh_idx < remote_add_idx
    )
    before_dolt_push = (
        create_fresh_idx != -1
        and dolt_push_idx != -1
        and create_fresh_idx < dolt_push_idx
    )
    return (
        metadata_ref,
        create_fresh_idx,
        adopts_committed_prefix,
        before_remote_add,
        before_dolt_push,
    )


_GAPH_SYNC_REMOTE_LINE = (
    'sync.remote: "git+https://github.com/dstengle/shopsystem-knowledge-beads.git"'
)


def _run_gaph_seed_body(script: str, workspace, probe_dir):
    """Execute the seed's create-fresh/seed body (committed-prefix extraction
    through the dolt seed, minus the live `git ls-remote` verify tail) with `bd`
    stubbed to record `.beads/config.yaml` at each step it runs, so the EXECUTED
    unconfigure -> init -> restore -> seed ordering can be asserted rather than
    mere string presence (lead-tc38 / GAP H)."""
    start = script.index("gapg_prefix=")
    end = script.index("git ls-remote", start)
    fragment = script[start:end]
    prelude = (
        f'PROBE="{probe_dir}"; '
        'bd() { '
        '  if [ "$1" = "init" ]; then cp .beads/config.yaml "$PROBE/at_init.yaml"; return 0; fi; '
        '  if [ "$1" = "dolt" ] && [ "$2" = "remote" ]; then cp .beads/config.yaml "$PROBE/at_remote_add.yaml"; return 0; fi; '
        '  if [ "$1" = "dolt" ] && [ "$2" = "push" ]; then cp .beads/config.yaml "$PROBE/at_push.yaml"; return 0; fi; '
        '  return 0; '
        '}; '
    )
    return subprocess.run(
        ["bash", "-c", prelude + fragment],
        cwd=str(workspace),
        capture_output=True,
        text=True,
    )


def _model_gaph_bd_init_outcome(sync_remote_configured_at_init: bool) -> dict:
    """Reference model of the negative control (the scenario's last And): if
    `bd init -p` runs WHILE `sync.remote` is configured to the empty remote it
    CLONES that remote and HARD-FAILS "contains no Dolt data"; only with
    sync.remote UNCONFIGURED does create-fresh succeed (lead-tc38 / GAP H)."""
    if sync_remote_configured_at_init:
        return {"bd_init": "clone-hard-fail", "bd_create": "issue_prefix-config-missing"}
    return {"bd_init": "create-fresh", "bd_create": "prefixed-id"}


_GAPI_SYNC_REMOTE_LINE = (
    'sync.remote: "git+https://github.com/dstengle/shopsystem-knowledge-beads.git"'
)


_GAPI_CLEAR_STMT = "rm -rf .beads/embeddeddolt"


def _gapi_stub_prelude(probe_dir):
    """A `bd` stub that FAITHFULLY mimics real bd's partial-DB behavior: `bd
    init` ABORTS non-zero ("database already exists; use bd init --force") when
    `.beads/embeddeddolt` is present, and CREATE-FRESHES (mkdir + CREATED_FRESH
    marker) only when absent; each observed sub-command records probe state so
    the EXECUTED clear -> init -> seed ordering can be asserted (lead-372r)."""
    return (
        f'PROBE="{probe_dir}"; '
        'bd() { '
        '  if [ "$1" = "init" ]; then '
        '    if [ -d .beads/embeddeddolt ]; then '
        '      printf present-aborted > "$PROBE/at_init"; '
        '      echo "database already exists; use bd init --force" >&2; '
        '      return 1; '
        '    fi; '
        '    mkdir -p .beads/embeddeddolt; '
        '    : > .beads/embeddeddolt/CREATED_FRESH; '
        '    printf absent-created > "$PROBE/at_init"; '
        '    return 0; '
        '  fi; '
        '  if [ "$1" = "dolt" ] && [ "$2" = "push" ]; then '
        '    if [ -f .beads/embeddeddolt/CREATED_FRESH ]; then '
        '      printf seeded > "$PROBE/at_push"; '
        '    else printf nothing > "$PROBE/at_push"; fi; '
        '    return 0; '
        '  fi; '
        '  return 0; '
        '}; '
    )


def _gapi_run_seed_body(script, workspace, probe_dir):
    """Execute the seed's create-fresh/seed body (committed-prefix extraction
    through the dolt seed, minus the live `git ls-remote` verify tail) against a
    fixture whose `.beads/embeddeddolt` already exists, with `bd` stubbed to the
    faithful partial-DB behavior (lead-372r / GAP I)."""
    start = script.index("gapg_prefix=")
    end = script.index("git ls-remote", start)
    fragment = script[start:end]
    return subprocess.run(
        ["bash", "-c", _gapi_stub_prelude(probe_dir) + fragment],
        cwd=str(workspace),
        capture_output=True,
        text=True,
    )


def _b3f0_dispatcher_toml_text():
    """The REAL committed dispatcher.toml bytes (the poured .toml entrypoint),
    via the launcher's own def-asset root."""
    path = _ky63_def_asset_root() / "dispatcher.toml"
    assert path.is_file(), (
        f"the poured dispatcher.toml entrypoint is ABSENT at {path}; the .toml "
        "entrypoint must ship in the launcher's fabro-def bundle (ADR-058)"
    )
    return path.read_text()


_B3F0_DISPATCHER_NODES = {"start", "end", "poll", "dispatch", "wait"}


def _b3f0_dispatcher_graph_text():
    """The REAL committed dispatcher.fabro bytes (the poured graph def), via the
    launcher's own def-asset root."""
    path = _ky63_def_asset_root() / "dispatcher.fabro"
    assert path.is_file(), (
        f"the poured dispatcher.fabro graph def is ABSENT at {path}; the native "
        "poll-loop dispatcher def must ship in the launcher's fabro-def bundle "
        "(ADR-058 AMENDED)"
    )
    return path.read_text()


def _b3f0_dispatcher_edges(graph: str):
    """[(src, dst, attr_block)] for edges between dispatcher nodes, quote-aware
    and comment-stripped (mirrors _ky63_parse_edges, filtered to the dispatcher
    node set)."""
    lc = _ky63_strip_line_comments(graph)
    inner = lc[lc.index("{") + 1:lc.rindex("}")]
    edges = []
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)", inner):
        src, dst = m.group(1), m.group(2)
        k = m.end()
        while k < len(inner) and inner[k] in " \t\n":
            k += 1
        attr = ""
        if k < len(inner) and inner[k] == "[":
            depth = 0
            inq = False
            j = k
            while j < len(inner):
                c = inner[j]
                if inq:
                    if c == "\\":
                        j += 2
                        continue
                    if c == '"':
                        inq = False
                else:
                    if c == '"':
                        inq = True
                    elif c == "[":
                        depth += 1
                    elif c == "]":
                        depth -= 1
                        if depth == 0:
                            attr = inner[k:j + 1]
                            break
                j += 1
        if src in _B3F0_DISPATCHER_NODES and dst in _B3F0_DISPATCHER_NODES:
            edges.append((src, dst, attr))
    return edges


def _b3f0_native_body(nodes, name):
    """Assert node ``name`` is a NATIVE script= node (parallelogram, no LLM) and
    return its attr body."""
    assert name in nodes, f"dispatcher.fabro missing native node {name!r}"
    body = nodes[name]
    assert "script=" in body, f"the {name!r} node must be a NATIVE script= node; body:\n{body}"
    assert "shape=parallelogram" in body, (
        f"the {name!r} node must be shape=parallelogram (native); body:\n{body}"
    )
    assert "prompt=" not in body and "class=" not in body, (
        f"the {name!r} node must have NO LLM (no prompt=/class=); body:\n{body}"
    )
    return body


def _b3f0_graph(ctx):
    return ctx.get("b3f0_dispatcher_graph") or _b3f0_dispatcher_graph_text()


import inspect as _l3zzu_inspect


_L3ZZU_STEP = {
    'acp_when': 'the poured "dispatcher.fabro" def\'s "dispatch" node is inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault',
    'acp_then_kind': 'the "dispatch" node is an ACP-backed AGENT node carrying "backend=acp" together with an "acp.command" attr (a shell such as "python3 <dispatch_acp_agent.py>") OR an "acp.config" attr (a JSON stdio config), so fabro drives it through the agent-client-protocol backend',
    'acp_then_notnative': 'the "dispatch" node is NOT a native "script="/parallelogram command node, so the pre-fix context-blind command dispatch is absent',
    'acp_then_receive': 'the "dispatch" node is wired to RECEIVE the incoming context yielded by the "poll" node — the pending inbox work ids plus the in-flight run state — as its input',
    'acp_then_return': 'the "dispatch" node is wired to RETURN structured dispatch DECISIONS as its output, which the loop consumes to spawn children, so the dispatch step both reads context and emits decisions rather than blindly re-acting on raw work ids each cycle',
}


def _l3zzu_dispatch_body(ctx):
    graph = ctx.get("l3zzu_graph") or _b3f0_dispatcher_graph_text()
    nodes = _ky63_parse_nodes(graph)
    assert "dispatch" in nodes, "dispatcher.fabro missing the `dispatch` node"
    return nodes["dispatch"]


def _l3zzu_load_acp_agent():
    """Import the poured NON-LLM ACP dispatch script-agent module from the
    fabro-def asset root.  Its decision contract (decide / DispatchTracker /
    materialize_child_config / spawn_command) is directly unit-testable because
    it is a plain python script, not an LLM: feed it context, assert decisions.
    """
    path = _ky63_def_asset_root() / "dispatch_acp_agent.py"
    assert path.is_file(), (
        f"the NON-LLM ACP dispatch script-agent is ABSENT at {path}; the ACP "
        "dispatch node's acp.command must point at a poured dispatch_acp_agent.py "
        "(ADR-058 Amendment 2, lead-3zzu)"
    )
    # compile+exec into a fresh module namespace rather than SourceFileLoader,
    # so loading the poured asset writes NO __pycache__ into the def-bundle tree
    # (the bundle-shape test asserts the asset root holds EXACTLY the enumerated
    # files -- a stray .pyc would be a spurious extra).
    import types as _types
    mod = _types.ModuleType("dispatch_acp_agent")
    mod.__file__ = str(path)
    exec(compile(path.read_text(), str(path), "exec"), mod.__dict__)
    return mod


_L3ZZU_IDEMP = {
    'given_container': 'the container "bc-shopsystem-messaging" is running with the self-contained fabro def set POURED by shop-templates into "/workspace/.fabro/", including the "dispatcher.fabro" graph def whose "dispatch" node is the ACP-backed agent node',
    'given_context': 'the incoming context carries a pending work id "W" AND the in-flight run state records that a prior child for "W" is still running and has not yet emitted work_done',
    'when': 'the ACP-backed "dispatch" node\'s decision contract is inspected structurally against that context, without a live docker daemon, a running fabro server, or a reachable agent-vault',
    'then_skip': 'the decision returned for the still-in-flight work id "W" is to SKIP re-dispatch, so NO second child is spawned for "W" while its prior child is live, and the two children cannot collide on the shared per-"W" git worktree',
    'then_spawn': 'when the in-flight run state records NO live child for a pending work id "V", the decision returned for "V" is to SPAWN a child, so a genuinely unstarted work id is still dispatched exactly once',
    'then_negctl': 'as the negative control, the pre-fix native command "dispatch" node carried NO in-flight skip and re-dispatched every still-pending work id each ~6s cycle — the exact duplicate-spawn the ACP node\'s in-flight tracking exists to eliminate',
}


_L3ZZU_DELIVERY = {
    'given_container': 'the container "bc-shopsystem-messaging" is running with the self-contained fabro def set POURED by shop-templates into "/workspace/.fabro/", including the "dispatcher.fabro" graph def whose "dispatch" node is the ACP-backed agent node and the UNCHANGED ADR-051 child def',
    'given_spawn': 'the ACP dispatch node\'s decision for a pending work id "W" with no live child is to SPAWN a child',
    'when': 'the ACP-backed "dispatch" node\'s decision contract and the per-child config it materializes for "W" are inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault',
    'then_overlay': 'the per-child config the ACP node materializes for "W" carries the CONCRETE work id in a "[run.environment.env]" overlay as "WORK_ID=W", so the child receives its own work id through the child config env overlay',
    'then_detached': 'the ACP node spawns that child DETACHED, so decided children run in PARALLEL isolated per WORK_ID and the dispatch step does not block on them before the loop\'s "wait -> poll" back-edge',
    'then_child_reaches': 'the spawned child runs the UNCHANGED ADR-051 child def, and the concrete "WORK_ID=W" from the "[run.environment.env]" overlay REACHES that child\'s native "script=" node env so the child acts on its own work id, preserving the lead-b3f0 delivery guarantee under the ACP dispatch',
}

# ---------------------------------------------------------------------------
# Step-definition modules: discovered dynamically. Drop a module in
# tests/steps/ and it is registered — no manual list to forget.
# Step defs must NOT be added to this file (enforced by tests/steps/test_step_hygiene.py).
# ---------------------------------------------------------------------------
from pathlib import Path as _Path

pytest_plugins = sorted(
    f"tests.steps.{p.stem}"
    for p in (_Path(__file__).parent / "steps").glob("*.py")
    if not p.stem.startswith(("_", "test_"))
)
