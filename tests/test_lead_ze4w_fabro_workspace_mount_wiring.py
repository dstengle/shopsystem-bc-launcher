"""Structural tests pinning the 4 launcher-wiring bugs fixed under lead-ze4w
(P-blocking request_bugfix) on the `--orchestrator fabro` / `--workspace-mount`
launch path.

FIDELITY (test-fidelity-for-image-layer-container-runtime-scenarios): every
assertion binds to the REAL launcher's recorded exec/write records over the
FakeDockerDriver — the actual `exec_calls` the controller issues on the
workspace-mount fabro path — NEVER a model.  Each test carries TEETH: reverting
the corresponding fix in controller.py makes it RED.

The four bugs (all structurally verifiable from the launcher's issued
commands/writes — no live container / docker / fabro / agent-vault):

  BUG#1 — the fabro def/shim/settings placement lived INSIDE the clone-only
          guard `if repo_url and not workspace_mount`, so a `--workspace-mount`
          fabro launch SKIPPED it (no /workspace/.fabro) yet still ran
          `fabro engage`.  FIX: placement is hoisted OUT of the clone guard so
          it runs on the workspace-mount fabro path too.
  BUG#2 — the placed workflow.toml carried the bundle defaults
          BC_NAME=fabro-throwaway / WORK_ID=fabro-spike-demo-3 in
          [run.environment.env] (read by native script= nodes as $BC_NAME /
          $WORK_ID) and [run.inputs].  FIX: the launcher rewrites them to the
          launch's ACTUAL bc_name / work_id.
  BUG#3 — the shim + engage run via non-login /bin/sh, so SSL_CERT_FILE (from
          the login profile) is empty and the shim's urllib does not trust the
          agent-vault MITM CA.  FIX: the launcher exports
          SSL_CERT_FILE=/home/vscode/.config/agent-vault/ca.pem on the shim +
          engage exec env.
  BUG#4 — `fabro server start` needs a server-level ~/.fabro config; the
          launcher wrote only the workflow-level settings, so the server
          aborted `server.auth.methods: field is required`.  FIX: the engage
          bootstraps the server (fabro install + anthropic provider at the shim
          base_url + dummy key) BEFORE `fabro server start` / `fabro run`.

The launch is driven against the REAL launcher (controller.launch(
workspace_mount=..., launch_path="fabro")) over the FakeDockerDriver.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

from bc_launcher.controller import (
    AGENT_CONTAINER_USER,
    AGENT_VAULT_CONTAINER_CA_PATH,
    ANTHROPIC_OAUTH_SHIM_BIN,
    BcContainerController,
    FABRO_ANTHROPIC_BASE_URL,
    FABRO_DEF_CONTAINER_DIR,
    FABRO_SERVER_DUMMY_ANTHROPIC_KEY,
    FABRO_SERVER_INSTALL_GITHUB_USERNAME,
    FABRO_SERVER_SETTINGS_CONTAINER_PATH,
    FABRO_SETTINGS_CONTAINER_PATH,
    FABRO_SHIM_PORT,
    FABRO_WORKFLOW_TOML_CONTAINER_PATH,
    FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME,
    FABRO_WORKFLOW_TOML_DEFAULT_WORK_ID,
    SSL_CERT_FILE_ENV,
    _fabro_def_asset_root,
)
from tests.fake_driver import FakeDockerDriver


BC_NAME = "shopsystem-messaging"
WORK_ID = "lead-ze4w-work-42"
HOST_TREE = "/host/live/shopsystem-messaging"


# ---------------------------------------------------------------------------
# Harness — drive the REAL launcher on the WORKSPACE-MOUNT + FABRO path
# ---------------------------------------------------------------------------

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


def _launch_workspace_mount_fabro(tmp_path: Path) -> FakeDockerDriver:
    """Drive the REAL launcher on the workspace-mount fabro path.

    No repo_url (bind-mounted host tree), launch_path="fabro", a concrete
    work_id.  Returns the FakeDockerDriver whose exec_calls are the launcher's
    ACTUAL recorded commands/writes.
    """
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
        f"workspace-mount fabro launch failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    return driver


def _container(bc_name: str = BC_NAME) -> str:
    return f"bc-{bc_name}"


# --- record locators (bound to the launcher's ACTUAL exec_calls) -----------

def _def_placement_call(driver: FakeDockerDriver):
    for c in driver.exec_calls:
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and f"{FABRO_DEF_CONTAINER_DIR}/workflow.fabro" in c.command[2]
            and "base64 -d" in c.command[2]
        ):
            return c
    return None


def _workflow_toml_write_call(driver: FakeDockerDriver):
    """The STANDALONE workflow.toml (re)write exec (BUG#2) — distinct from the
    15-file def-bundle placement, which also writes workflow.toml.  The rewrite
    exec targets ONLY workflow.toml (never workflow.fabro / other def files)."""
    for c in driver.exec_calls:
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and FABRO_WORKFLOW_TOML_CONTAINER_PATH in c.command[2]
            and "base64 -d" in c.command[2]
            # Exclude the def-bundle placement (it also writes workflow.fabro
            # and the nodes/ subtree in the SAME script).
            and f"{FABRO_DEF_CONTAINER_DIR}/workflow.fabro" not in c.command[2]
        ):
            return c
    return None


def _shim_start_call(driver: FakeDockerDriver):
    for c in driver.exec_calls:
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and ANTHROPIC_OAUTH_SHIM_BIN in c.command[2]
            and f"--port {FABRO_SHIM_PORT}" in c.command[2]
        ):
            return c
    return None


def _settings_write_call(driver: FakeDockerDriver):
    for c in driver.exec_calls:
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and FABRO_SETTINGS_CONTAINER_PATH in c.command[2]
            and "base64 -d" in c.command[2]
        ):
            return c
    return None


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


def _recover_written_bytes(script: str) -> str:
    """Recover the base64-decoded file bytes a write script installs."""
    m = re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \| base64 -d", script)
    assert m, f"could not recover base64 payload from script: {script!r}"
    return base64.b64decode(m.group(1)).decode("utf-8")


# ===========================================================================
# BUG#1 — placement executes on the --workspace-mount path (not only clone)
# ===========================================================================

def test_bug1_def_placement_runs_on_workspace_mount_fabro_path(tmp_path):
    """(i) The def/shim/settings placement EXECUTES on the workspace-mount
    fabro path — the launcher issues the def-bundle placement, the shim start,
    and the settings write even though NO clone ran.

    TEETH: revert BUG#1 (put the placement back inside
    `if repo_url and not workspace_mount:`) -> on the workspace-mount path
    these calls are never issued -> the locators return None -> RED.
    """
    driver = _launch_workspace_mount_fabro(tmp_path)

    # No clone ran (workspace-mount): confirm the guard the bug lived under is
    # genuinely not taken, so placement running is NOT a clone-path artifact.
    clone_calls = [
        c for c in driver.exec_calls
        if c.container == _container() and c.command[:2] == ["git", "clone"]
    ]
    assert not clone_calls, (
        "workspace-mount launch must not clone; placement below must therefore "
        "run OUTSIDE the clone guard"
    )

    assert _def_placement_call(driver) is not None, (
        "BUG#1: the fabro def bundle must be placed on the workspace-mount "
        "fabro path (no /workspace/.fabro otherwise)"
    )
    assert _shim_start_call(driver) is not None, (
        "BUG#1: the shim must be started on the workspace-mount fabro path"
    )
    assert _settings_write_call(driver) is not None, (
        "BUG#1: fabro settings must be written on the workspace-mount fabro "
        "path"
    )


def test_bug1_placement_does_not_disturb_mounted_tree(tmp_path):
    """The workspace-mount byte-unchanged invariant (lead-zxtk) still holds:
    placement runs, but NO bd bootstrap / shop-templates re-pour writes the
    mounted host tree, and the launcher-created .fabro/ chown is scoped to the
    .fabro/ subtree ONLY (never a recursive chown of the mounted /workspace).
    """
    driver = _launch_workspace_mount_fabro(tmp_path)
    assert not driver.bd_bootstrap_ran(_container())
    assert not driver.shop_templates_update_ran(_container())
    # No `chown -R vscode:vscode /workspace` on the workspace-mount path (that
    # would recursively touch the live mounted tree).  The only recursive chown
    # targets the launcher-created .fabro/ subtree.
    recursive_ws_chowns = [
        c for c in driver.exec_calls
        if c.command
        and c.command[0] == "chown"
        and "-R" in c.command
        and any(a.rstrip("/") == "/workspace" for a in c.command)
    ]
    assert not recursive_ws_chowns, (
        "a workspace-mount launch must not recursively chown the mounted "
        f"/workspace tree; got: {[c.command for c in recursive_ws_chowns]}"
    )


# ===========================================================================
# BUG#2 — placed workflow.toml BC_NAME/WORK_ID rewritten to ACTUAL values
# ===========================================================================

def test_bug2_workflow_toml_rewritten_to_actual_bc_name_and_work_id(tmp_path):
    """(ii) The placed workflow.toml [run.environment.env] AND [run.inputs]
    BC_NAME/WORK_ID equal the launch's ACTUAL bc_name/work_id — NOT the bundle
    defaults fabro-throwaway / fabro-spike-demo-3.

    Bound to the launcher's REAL write: recover the exact bytes the launcher's
    workflow.toml write exec base64-decodes into the placed path.

    TEETH: revert BUG#2 (drop the workflow.toml rewrite) -> the placed
    workflow.toml keeps the bundle defaults -> the default-absent assertions
    and the actual-present assertions both fail -> RED.
    """
    driver = _launch_workspace_mount_fabro(tmp_path)
    call = _workflow_toml_write_call(driver)
    assert call is not None, (
        "BUG#2: the launcher must issue a workflow.toml (re)write on the fabro "
        "path"
    )
    written = _recover_written_bytes(call.command[2])

    # The bundle-default identity is GONE from both tables.
    assert FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME not in written, (
        "BUG#2: the placed workflow.toml still carries the bundle-default "
        f"BC_NAME {FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME!r}"
    )
    assert FABRO_WORKFLOW_TOML_DEFAULT_WORK_ID not in written, (
        "BUG#2: the placed workflow.toml still carries the bundle-default "
        f"WORK_ID {FABRO_WORKFLOW_TOML_DEFAULT_WORK_ID!r}"
    )

    # The ACTUAL identity is present.  The asset carries BC_NAME / WORK_ID in
    # BOTH [run.inputs] and [run.environment.env]; the rewrite must hit both,
    # so we require TWO occurrences of each (one per table).
    assert written.count(f'BC_NAME = "{BC_NAME}"') >= 2, (
        "BUG#2: the ACTUAL BC_NAME must appear in BOTH [run.inputs] and "
        f"[run.environment.env]; got written:\n{written}"
    )
    assert written.count(f'WORK_ID = "{WORK_ID}"') >= 2, (
        "BUG#2: the ACTUAL WORK_ID must appear in BOTH [run.inputs] and "
        f"[run.environment.env]; got written:\n{written}"
    )

    # The rewrite is scoped to identity only — the [run.environment.env] table
    # header is preserved (proving we rewrote the overlay the native script=
    # nodes read $BC_NAME / $WORK_ID from, not some unrelated key).
    assert "[run.environment.env]" in written
    assert "[run.inputs]" in written


def test_bug2_asset_still_ships_the_bundle_defaults(tmp_path):
    """Guard the teeth: the packaged asset genuinely ships the bundle defaults,
    so the BUG#2 rewrite is doing real work (not asserting against an asset
    that already carries the actual identity)."""
    asset = (_fabro_def_asset_root() / "workflow.toml").read_text()
    assert FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME in asset
    assert FABRO_WORKFLOW_TOML_DEFAULT_WORK_ID in asset


# ===========================================================================
# BUG#3 — SSL_CERT_FILE on the shim + engage exec env
# ===========================================================================

def test_bug3_shim_exec_env_carries_ssl_cert_file(tmp_path):
    """(iii) The shim exec env includes
    SSL_CERT_FILE=/home/vscode/.config/agent-vault/ca.pem.

    TEETH: revert BUG#3 (drop the SSL_CERT_FILE export on the shim exec) ->
    the shim ExecCall.env lacks SSL_CERT_FILE -> RED.
    """
    driver = _launch_workspace_mount_fabro(tmp_path)
    call = _shim_start_call(driver)
    assert call is not None, "BUG#1/#3: shim start exec must be present"
    assert (call.env or {}).get(SSL_CERT_FILE_ENV) == AGENT_VAULT_CONTAINER_CA_PATH, (
        "BUG#3: the shim exec env must pin SSL_CERT_FILE to the materialized "
        f"broker CA path; got env={call.env!r}"
    )


def test_bug3_engage_exec_env_carries_ssl_cert_file(tmp_path):
    """(iii) The `fabro engage` exec env includes SSL_CERT_FILE at the broker
    CA path — both as the recorded ExecCall.env AND exported inside the engage
    script (the non-login /bin/sh that runs the shim-backed server + run).

    TEETH: revert BUG#3 (drop the SSL_CERT_FILE from _fabro_exec_env / the
    engage script export) -> the engage exec lacks it -> RED.
    """
    driver = _launch_workspace_mount_fabro(tmp_path)
    call = _engage_call(driver)
    assert call is not None, "BUG#4: engage exec (server start + run) present"
    assert (call.env or {}).get(SSL_CERT_FILE_ENV) == AGENT_VAULT_CONTAINER_CA_PATH, (
        "BUG#3: the engage exec env must pin SSL_CERT_FILE to the broker CA "
        f"path; got env={call.env!r}"
    )
    assert (
        f"export {SSL_CERT_FILE_ENV}={AGENT_VAULT_CONTAINER_CA_PATH}"
        in call.command[2]
    ), (
        "BUG#3: the engage script must export SSL_CERT_FILE so the "
        "server/run subprocesses inherit the CA trust"
    )


# ===========================================================================
# BUG#4 — server-level fabro bootstrap BEFORE `fabro server start` / `fabro run`
# ===========================================================================

def test_bug4_engage_bootstraps_server_before_start_and_run(tmp_path):
    """(iv) `_fabro_engage` bootstraps a server-level fabro config — `fabro
    install --non-interactive --skip-llm --overwrite-settings` + an anthropic
    provider at the shim base_url + a dummy key — BEFORE `fabro server start`
    and `fabro run`.

    TEETH: revert BUG#4 (drop the install + provider bootstrap from
    _fabro_engage_script) -> the engage script starts the server with no
    server-level config -> the `fabro install` token is absent / not ordered
    before `fabro server start` -> RED.
    """
    driver = _launch_workspace_mount_fabro(tmp_path)
    call = _engage_call(driver)
    assert call is not None, "BUG#4: engage exec must be present"
    script = call.command[2]

    install_pos = script.find("fabro install")
    start_pos = script.find("fabro server start")
    run_pos = script.find("fabro run")

    assert install_pos != -1, (
        "BUG#4: the engage must run `fabro install` to write a valid "
        f"server-level ~/.fabro config; script:\n{script}"
    )
    # The install carries the non-interactive / skip-llm / overwrite flags.
    for flag in ("--non-interactive", "--skip-llm", "--overwrite-settings"):
        assert flag in script, f"BUG#4: `fabro install` must carry {flag}"

    assert start_pos != -1 and run_pos != -1, (
        "BUG#4: the engage must still start the server and run the def"
    )
    assert install_pos < start_pos, (
        "BUG#4: `fabro install` (server bootstrap) must run BEFORE "
        "`fabro server start`"
    )
    assert install_pos < run_pos, (
        "BUG#4: the server bootstrap must run BEFORE `fabro run`"
    )

    # The anthropic provider is pointed at the shim base_url with a DUMMY key
    # exported in the server env (ADR-049 D1 — real cred rides agent-vault).
    assert FABRO_ANTHROPIC_BASE_URL in script, (
        "BUG#4: the server bootstrap must point the anthropic provider at the "
        f"shim base_url {FABRO_ANTHROPIC_BASE_URL}"
    )
    assert "ANTHROPIC_API_KEY=" in script, (
        "BUG#4: a dummy ANTHROPIC_API_KEY must be exported in the server env"
    )


def test_bug4_dummy_key_is_not_a_real_credential(tmp_path):
    """ADR-049 invariant: the ANTHROPIC_API_KEY the engage exports is a DUMMY
    placeholder, never a real credential — the real cred rides agent-vault on
    the wire.  A defensive teeth-guard so a future edit cannot substitute a
    real-looking key without tripping this test."""
    driver = _launch_workspace_mount_fabro(tmp_path)
    call = _engage_call(driver)
    assert call is not None
    script = call.command[2]
    m = re.search(r"ANTHROPIC_API_KEY=([^\s]+)", script)
    assert m, "expected an ANTHROPIC_API_KEY export in the engage script"
    key = m.group(1).strip("'\"")
    assert "dummy" in key.lower(), (
        f"the exported ANTHROPIC_API_KEY must be an explicit dummy; got {key!r}"
    )


# ===========================================================================
# (v) ADDITIVE — the tmux default (non-fabro) launch path is UNCHANGED
# ===========================================================================

def test_tmux_workspace_mount_launch_issues_no_fabro_wiring(tmp_path):
    """(v) A workspace-mount launch on the DEFAULT (tmux) orchestrator issues
    NO fabro def placement, NO shim start, NO settings write, NO engage — the
    fabro wiring is strictly gated on launch_path == fabro.

    TEETH: if the placement were hoisted WITHOUT the launch_path==fabro gate,
    this tmux launch would issue fabro writes -> RED.
    """
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
        # launch_path defaults to "tmux"
        manifest_path=_make_manifest(tmp_path),
        credential_home=_make_credential_home(tmp_path),
    )
    assert result.exit_code == 0, (
        f"tmux workspace-mount launch failed: stderr={result.stderr!r}"
    )
    assert _def_placement_call(driver) is None, (
        "tmux default: no fabro def placement"
    )
    assert _workflow_toml_write_call(driver) is None, (
        "tmux default: no workflow.toml rewrite"
    )
    assert _shim_start_call(driver) is None, "tmux default: no shim start"
    assert _settings_write_call(driver) is None, "tmux default: no settings"
    assert _engage_call(driver) is None, "tmux default: no fabro engage"


# ===========================================================================
# lead-8q2x — the ze4w Fix#4 server-bootstrap was broken 3 ways at runtime
# (proven at the lead's v0.3.46 clean e2e: engage exited 1 with "x
# non-interactive install requires --github-strategy" AND "x workflow not
# found: /workspace/workflow.fabro").  Each fix below is pinned STRUCTURALLY
# against the REAL launcher-recorded engage script over FakeDockerDriver
# (test-fidelity-for-image-layer-container-runtime-scenarios), each with
# TEETH: reverting the fix in controller.py makes it RED.
# ===========================================================================

def test_8q2x_defect_a_install_carries_github_strategy_and_username(tmp_path):
    """(i) Defect A — INSTALL-FLAG DRIFT.  `fabro install
    --non-interactive --skip-llm --overwrite-settings` ABORTS on fabro
    0.254.0 with "x non-interactive install requires --github-strategy".
    The engage install must now carry `--github-strategy token` AND
    `--github-username <dummy>` with a GH_TOKEN provided inline.

    TEETH: drop `--github-strategy token` / `--github-username` from
    `_fabro_server_install_argv` -> the tokens are absent from the recorded
    engage script -> RED (and the real install would re-abort on the flag
    chain).
    """
    driver = _launch_workspace_mount_fabro(tmp_path)
    call = _engage_call(driver)
    assert call is not None, "engage exec must be present"
    script = call.command[2]

    # The fabro install must run BEFORE the flag chain aborts: the corrected
    # recipe carries --github-strategy token + --github-username.
    assert "--github-strategy token" in script, (
        "Defect A: engage `fabro install` must carry `--github-strategy "
        f"token`; script:\n{script}"
    )
    assert (
        f"--github-username {FABRO_SERVER_INSTALL_GITHUB_USERNAME}" in script
    ), (
        "Defect A: engage `fabro install` must carry `--github-username "
        f"<dummy>`; script:\n{script}"
    )
    # GH_TOKEN must be provided (inline env) for the token strategy.
    gh_pos = script.find("GH_TOKEN=")
    install_pos = script.find("fabro install")
    assert gh_pos != -1, (
        "Defect A: a GH_TOKEN must be provided for the token strategy"
    )
    assert gh_pos < install_pos, (
        "Defect A: GH_TOKEN must be provided in the install's env (before "
        f"`fabro install`); script:\n{script}"
    )
    # The prior working flags are preserved.
    for flag in ("--non-interactive", "--skip-llm", "--overwrite-settings"):
        assert flag in script, f"Defect A: install must still carry {flag}"


def test_8q2x_defect_b_run_resolves_def_dir_workflow_not_workspace_root(
    tmp_path,
):
    """(ii) Defect B — & CWD-SCOPING.  The trailing `&` previously
    backgrounded the WHOLE `cd /workspace/.fabro && install && ... && nohup
    server` AND-list, so `cd` ran inside the backgrounded subshell and the
    PARENT shell cwd stayed /workspace (the image WORKDIR) -> `fabro run
    workflow.fabro` resolved /workspace/workflow.fabro -> "x workflow not
    found".  The corrected script runs `cd {def_dir}` + install SYNCHRONOUSLY
    in the SAME shell as `fabro run`, backgrounding ONLY the server.

    Structural resolution model: the corrected script is ONE synchronous
    `&&` chain starting with `cd /workspace/.fabro`, with ONLY the foreground
    server detached inside a brace group `{ nohup ... & }`, and `fabro run`
    chained via `&&` after it — so the `cd`, install, provider-register and
    `fabro run` all execute in the SAME shell whose cwd is /workspace/.fabro.
    Assert (1) the chain begins with a synchronous `cd /workspace/.fabro &&`,
    (2) ONLY the server is inside the backgrounded brace group — the install
    and `fabro run` are NOT backgrounded, and (3) the launcher does NOT resolve
    /workspace/workflow.fabro (the WORKDIR-root path the bug produced).

    TEETH: background the whole AND-list (terminate `cd && install && ... &&
    nohup server` with a bare trailing `&`, so the `cd` runs in the
    backgrounded subshell) / drop the `cd` so the relative `workflow.fabro`
    resolves against the image WORKDIR -> the `fabro run` no longer runs with
    cwd=/workspace/.fabro -> RED.
    """
    driver = _launch_workspace_mount_fabro(tmp_path)
    call = _engage_call(driver)
    assert call is not None, "engage exec must be present"
    script = call.command[2]

    cd_def = f"cd {FABRO_DEF_CONTAINER_DIR}"
    assert cd_def in script, (
        f"Defect B: engage must `cd` into the def dir; script:\n{script}"
    )

    # (1) The chain is SYNCHRONOUS and begins with `cd /workspace/.fabro &&`
    # so the cwd persists to `fabro run`.
    assert script.lstrip().startswith(f"{cd_def} &&"), (
        "Defect B: the engage must `cd /workspace/.fabro` FIRST as a "
        "synchronous step (chained by `&&`), so the parent shell cwd "
        f"persists to `fabro run`; script:\n{script}"
    )

    # (2) ONLY the foreground server is detached, inside a brace group
    # `{ nohup ... server ... & }`.  The `&` that backgrounds must belong to
    # that brace group — NOT to a bare trailing `&` on the whole AND-list
    # (which would background the `cd` too and leave the parent cwd at the
    # image WORKDIR).
    m = re.search(r"\{\s*nohup [^}]*fabro server start[^}]*&\s*\}", script)
    assert m is not None, (
        "Defect B: ONLY the server must be backgrounded, inside a brace "
        "group `{ nohup fabro server start ... & }` — so the `cd`+install "
        f"stay synchronous in the parent shell; script:\n{script}"
    )
    server_bg = m.group(0)
    # The `cd` and the install are NOT inside the backgrounded server group.
    assert cd_def not in server_bg, (
        "Defect B: `cd /workspace/.fabro` must NOT be inside the backgrounded "
        f"server group; script:\n{script}"
    )
    assert "fabro install" not in server_bg, (
        "Defect B: `fabro install` must run SYNCHRONOUSLY, not inside the "
        f"backgrounded server group; script:\n{script}"
    )
    assert "fabro run" not in server_bg, (
        "Defect B: `fabro run` must NOT be backgrounded; it runs synchronously "
        f"in the foreground shell; script:\n{script}"
    )
    # The whole script must NOT end with a bare trailing `&` (that would
    # background the entire AND-list including the `cd`).
    assert not script.rstrip().endswith("&"), (
        "Defect B: the engage script must NOT terminate the whole AND-list "
        "with a bare trailing `&` (that backgrounds the `cd` and leaves the "
        f"parent cwd at the image WORKDIR); script:\n{script}"
    )

    # (3) `fabro run workflow.fabro` runs AFTER the backgrounded server group,
    # in the SAME (cwd=/workspace/.fabro) shell -> resolves
    # /workspace/.fabro/workflow.fabro, NOT /workspace/workflow.fabro.
    run_pos = script.find("fabro run workflow.fabro")
    assert run_pos != -1, (
        f"Defect B: engage must issue `fabro run workflow.fabro`; "
        f"script:\n{script}"
    )
    assert run_pos > m.start(), (
        "Defect B: `fabro run` must run AFTER the backgrounded server group, "
        f"in the foreground shell; script:\n{script}"
    )
    assert "/workspace/workflow.fabro" not in script, (
        "Defect B: the engage must NOT resolve /workspace/workflow.fabro "
        f"(the WORKDIR-root path the bug produced); script:\n{script}"
    )


def test_8q2x_defect_c_provider_registered_at_server_settings(tmp_path):
    """(iii) Defect C — PROVIDER NOT REGISTERED AT SERVER.  `--skip-llm`
    skips server-level provider registration, so `fabro model test` reported
    "not configured" even with the server up — the SERVER does not read the
    workflow-level settings for model resolution.  The bootstrap must register
    the anthropic provider AT THE SERVER by appending a
    `[llm.providers.anthropic]` block (shim base_url + DUMMY key) to the
    server-level ~/.fabro/settings.toml AFTER install.

    TEETH: drop the server-level provider registration (the append to the
    server settings) -> the `[llm.providers.anthropic]` block targeting the
    SERVER settings path is absent -> RED.
    """
    driver = _launch_workspace_mount_fabro(tmp_path)
    call = _engage_call(driver)
    assert call is not None, "engage exec must be present"
    script = call.command[2]

    # The provider block is written to the SERVER-level settings path
    # (~/.fabro/settings.toml), NOT the workflow-level one.
    assert FABRO_SERVER_SETTINGS_CONTAINER_PATH in script, (
        "Defect C: the engage must register the provider at the SERVER-level "
        f"settings ({FABRO_SERVER_SETTINGS_CONTAINER_PATH}); script:\n{script}"
    )
    # It is an APPEND (`>>`) to the server settings, AFTER install writes the
    # base config.
    assert f">> {FABRO_SERVER_SETTINGS_CONTAINER_PATH}" in script, (
        "Defect C: the provider block must be APPENDED (>>) to the "
        f"server-level settings; script:\n{script}"
    )
    server_settings_pos = script.find(FABRO_SERVER_SETTINGS_CONTAINER_PATH)
    install_pos = script.find("fabro install")
    assert install_pos < server_settings_pos, (
        "Defect C: the provider registration must run AFTER `fabro install` "
        f"writes the base ~/.fabro/settings.toml; script:\n{script}"
    )
    # The block registers the anthropic provider at the shim base_url with a
    # DUMMY key (ADR-049 D1 — real cred rides agent-vault on the wire).
    assert "[llm.providers.anthropic]" in script, (
        "Defect C: the appended block must register [llm.providers.anthropic]"
    )
    assert FABRO_ANTHROPIC_BASE_URL in script, (
        "Defect C: the server-level provider must point at the shim base_url "
        f"{FABRO_ANTHROPIC_BASE_URL}"
    )
    assert FABRO_SERVER_DUMMY_ANTHROPIC_KEY in script, (
        "Defect C: the server-level provider must carry the DUMMY key"
    )
    assert "dummy" in FABRO_SERVER_DUMMY_ANTHROPIC_KEY.lower(), (
        "Defect C: the server-level provider key must be an explicit DUMMY "
        "placeholder (ADR-049 D1 — no real cred literal)"
    )
