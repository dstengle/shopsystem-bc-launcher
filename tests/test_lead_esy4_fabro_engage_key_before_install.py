"""Structural teeth for lead-esy4 Defect D — the FINAL fabro engage bug.

ROOT CAUSE (recorded at the lead's clean e2e): `fabro install
--non-interactive --skip-llm --overwrite-settings --github-strategy token
--github-username x` STARTS the fabro serving daemon.  When the engage exported
ANTHROPIC_API_KEY (+ SSL_CERT_FILE + ANTHROPIC_BASE_URL) AFTER `fabro install`,
the install-spawned daemon had NO LLM key in its env; the later `fabro server
start` could not bind ("× Server already running", a no-op) and `fabro run`
targeted the keyless install-daemon whose preflight FAILED "No LLM providers
configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY".

FIX: the three exports must precede `fabro install` in the rendered engage
script, so the install-spawned daemon INHERITS the key (+ shim base_url + broker
CA) and `fabro run` preflight passes.

FIDELITY (test-fidelity-for-image-layer-container-runtime-scenarios): the
assertions bind to the REAL launcher's ACTUAL recorded engage exec over the
FakeDockerDriver (controller.launch(workspace_mount=..., launch_path="fabro")),
never a model.

TEETH: move any of the three exports back to AFTER `fabro install` in
`_fabro_engage_script` -> the export no longer precedes the install position ->
RED.  The ordering IS the fix.
"""
from __future__ import annotations

import re
from pathlib import Path

from bc_launcher.controller import (
    AGENT_VAULT_CONTAINER_CA_PATH,
    BcContainerController,
    FABRO_ANTHROPIC_BASE_URL,
    FABRO_SERVER_DUMMY_ANTHROPIC_KEY,
    SSL_CERT_FILE_ENV,
)
from tests.fake_driver import FakeDockerDriver


BC_NAME = "shopsystem-messaging"
WORK_ID = "lead-esy4-work-7"
HOST_TREE = "/host/live/shopsystem-messaging"


def _make_credential_home(tmp_path: Path) -> Path:
    home = tmp_path / "fake_home"
    home.mkdir()
    (home / ".claude").mkdir()
    (home / ".claude" / ".claude.json").write_text("{}")
    (home / ".config" / "gh").mkdir(parents=True)
    (home / ".gitconfig").write_text("")
    return home


def _make_manifest(tmp_path: Path) -> Path:
    manifest = tmp_path / "bc-manifest.yaml"
    manifest.write_text(
        "product: shopsystem product\n"
        "bcs:\n"
        f"  - name: {BC_NAME}\n"
        f"    remote: https://github.com/shopsystem/{BC_NAME}.git\n"
        "    role: bc\n"
    )
    return manifest


def _launch_fabro(tmp_path: Path) -> FakeDockerDriver:
    driver = FakeDockerDriver()
    driver.set_host_tree_snapshot(
        HOST_TREE,
        beads_registry='{"id":"seed-1","title":"committed"}\n',
        claude_skills="poured-skill-group/bc-router-health\n",
    )
    controller = BcContainerController(driver)
    result = controller.launch(
        bc_name=BC_NAME,
        repo_url=None,
        workspace_mount=HOST_TREE,
        launch_path="fabro",
        work_id=WORK_ID,
        manifest_path=_make_manifest(tmp_path),
        credential_home=_make_credential_home(tmp_path),
    )
    assert result.exit_code == 0, (
        f"fabro launch failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    return driver


def _engage_call(driver: FakeDockerDriver):
    for c in driver.exec_calls:
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and "fabro server start" in c.command[2]
            and "fabro run" in c.command[2]
        ):
            return c
    return None


def _engage_script(tmp_path: Path) -> str:
    driver = _launch_fabro(tmp_path)
    call = _engage_call(driver)
    assert call is not None, "the fabro engage exec (server start + run) must exist"
    return call.command[2]


# ---------------------------------------------------------------------------
# (i) / (iii) The three exports PRECEDE `fabro install` in the engage script.
# ---------------------------------------------------------------------------

def test_esy4_anthropic_api_key_exported_before_fabro_install(tmp_path):
    """The DUMMY ANTHROPIC_API_KEY export precedes `fabro install`, so the
    install-spawned serving daemon inherits the key and `fabro run` preflight
    passes ("No LLM providers configured" no longer fires).

    TEETH: move `export ANTHROPIC_API_KEY=...` back to AFTER `fabro install` ->
    key_pos > install_pos -> RED.
    """
    script = _engage_script(tmp_path)
    key_pos = script.find(f"export ANTHROPIC_API_KEY={FABRO_SERVER_DUMMY_ANTHROPIC_KEY}")
    install_pos = script.find("fabro install")
    assert key_pos != -1, (
        "the engage must export the DUMMY ANTHROPIC_API_KEY in the server env; "
        f"script:\n{script}"
    )
    assert install_pos != -1, f"the engage must run `fabro install`; script:\n{script}"
    assert key_pos < install_pos, (
        "lead-esy4 Defect D: `export ANTHROPIC_API_KEY=<dummy>` MUST precede "
        "`fabro install` so the install-spawned daemon inherits the LLM key "
        f"and `fabro run` preflight passes; script:\n{script}"
    )


def test_esy4_ssl_cert_file_exported_before_fabro_install(tmp_path):
    """SSL_CERT_FILE (broker CA) exported before `fabro install`, so the
    install-spawned daemon + its subprocesses trust the agent-vault MITM CA.

    TEETH: move the SSL_CERT_FILE export back to AFTER `fabro install` -> RED.
    """
    script = _engage_script(tmp_path)
    ssl_pos = script.find(
        f"export {SSL_CERT_FILE_ENV}={AGENT_VAULT_CONTAINER_CA_PATH}"
    )
    install_pos = script.find("fabro install")
    assert ssl_pos != -1, (
        f"the engage must export {SSL_CERT_FILE_ENV}={AGENT_VAULT_CONTAINER_CA_PATH}; "
        f"script:\n{script}"
    )
    assert ssl_pos < install_pos, (
        "lead-esy4 Defect D: SSL_CERT_FILE MUST be exported BEFORE `fabro "
        f"install`; script:\n{script}"
    )


def test_esy4_anthropic_base_url_exported_before_fabro_install(tmp_path):
    """ANTHROPIC_BASE_URL (the shim endpoint) exported before `fabro install`,
    so the install-spawned daemon routes anthropic traffic at the shim.

    TEETH: move the ANTHROPIC_BASE_URL export back to AFTER `fabro install` ->
    RED.
    """
    script = _engage_script(tmp_path)
    base_pos = script.find(f"export ANTHROPIC_BASE_URL={FABRO_ANTHROPIC_BASE_URL}")
    install_pos = script.find("fabro install")
    assert base_pos != -1, (
        "the engage must export ANTHROPIC_BASE_URL pointed at the shim "
        f"{FABRO_ANTHROPIC_BASE_URL}; script:\n{script}"
    )
    assert base_pos < install_pos, (
        "lead-esy4 Defect D: ANTHROPIC_BASE_URL MUST be exported BEFORE `fabro "
        f"install`; script:\n{script}"
    )


def test_esy4_all_three_exports_precede_install(tmp_path):
    """Composite ordering pin: the earliest `fabro install` occurrence comes
    AFTER all three exports (single assertion the lead can read as acceptance
    (i)/(iii))."""
    script = _engage_script(tmp_path)
    install_pos = script.find("fabro install")
    for token in (
        f"export {SSL_CERT_FILE_ENV}={AGENT_VAULT_CONTAINER_CA_PATH}",
        f"export ANTHROPIC_API_KEY={FABRO_SERVER_DUMMY_ANTHROPIC_KEY}",
        f"export ANTHROPIC_BASE_URL={FABRO_ANTHROPIC_BASE_URL}",
    ):
        pos = script.find(token)
        assert pos != -1 and pos < install_pos, (
            f"lead-esy4 Defect D: {token!r} must precede `fabro install`; "
            f"script:\n{script}"
        )


# ---------------------------------------------------------------------------
# (ii) scn-77 tension: the redundant server-start is KEPT (harmless,
#      backgrounded) because scn 77 pins the argv.  Assert it stays inside the
#      backgrounded brace group so its no-op does not break the `&&` chain.
# ---------------------------------------------------------------------------

def test_esy4_server_start_kept_backgrounded_and_after_exports(tmp_path):
    """The `fabro server start --foreground --no-web` argv is RETAINED (scn 77
    @scenario_hash:68e14cdcd8b7c145 pins it) but backgrounded inside the brace
    group AND placed after the env exports, so its "× Server already running"
    no-op returns 0 immediately and does not break the chain before `fabro run`.
    """
    script = _engage_script(tmp_path)
    # The pinned argv is present (scn 77 requires it).
    assert "fabro server start --foreground --no-web" in script, (
        "scn 77 pins `fabro server start --foreground --no-web` in the engage; "
        f"script:\n{script}"
    )
    # It is inside a backgrounded brace group `{ nohup ... & }`.
    m = re.search(r"\{\s*nohup [^}]*fabro server start[^}]*&\s*\}", script)
    assert m is not None, (
        "the retained server-start must be backgrounded inside `{ nohup ... & }` "
        f"so its no-op returns 0 and does not break the chain; script:\n{script}"
    )
    # The exports precede the backgrounded server group.
    key_pos = script.find(f"export ANTHROPIC_API_KEY={FABRO_SERVER_DUMMY_ANTHROPIC_KEY}")
    assert key_pos != -1 and key_pos < m.start(), (
        "the env exports must precede the (retained, backgrounded) server-start; "
        f"script:\n{script}"
    )
    # And `fabro run` runs after the server group.
    run_pos = script.find("fabro run")
    assert run_pos > m.start(), (
        "`fabro run` must run after the backgrounded server group; "
        f"script:\n{script}"
    )


def test_esy4_dummy_key_is_not_a_real_credential(tmp_path):
    """ADR-049 D1 invariant: the exported ANTHROPIC_API_KEY is a DUMMY
    placeholder even though it now precedes `fabro install`.  The real credential
    rides agent-vault on the wire; the fabro settings/vault stay __PLACEHOLDER__.
    """
    script = _engage_script(tmp_path)
    m = re.search(r"ANTHROPIC_API_KEY=([^\s]+)", script)
    assert m, "expected an ANTHROPIC_API_KEY export in the engage script"
    key = m.group(1).strip("'\"")
    assert "dummy" in key.lower(), (
        f"the exported ANTHROPIC_API_KEY must be an explicit dummy; got {key!r}"
    )
    # No api_key line is written into the settings TOML append (schema-invalid
    # on fabro 0.254.0; ADR-049 D1).  The only api-key-shaped token is the env
    # export.
    settings_append = re.search(
        r"printf\s+'%b'\s+('(?:[^']|'\\'')*')\s+>>",
        script,
    )
    if settings_append:
        assert "api_key" not in settings_append.group(1), (
            "no api_key may be written into the settings TOML append (ADR-049 D1 "
            "+ fabro 0.254.0 rejects it)"
        )
