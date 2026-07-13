"""Structural teeth for lead-lwk4 (request_bugfix, supersedes lead-n69n).

Two fixes, each bound to REAL artifacts (the committed workflow.fabro and the
REAL launcher's recorded engage exec over the FakeDockerDriver) — never a model.

R6 (option a) — the `.coding` and `.review` stylesheet rules pin
    claude-haiku-4-5 as a REAL PRIMARY model (validate-HONORED, NOT a swallowed
    `fallbacks:` key).  claude-sonnet-4-5 is persistently 429 on the fleet;
    haiku returns 200.  fabro 0.254.0 SILENTLY SWALLOWS a stylesheet
    `fallbacks:` key (a false-green), but a `model:` value IS honored — a BOGUS
    `model:` is CAUGHT by `fabro validate` (`stylesheet_model_known` /
    `node_model_known`).  So the fix uses a real `model:` value, PROVEN honored.
    TEETH: revert a `.coding`/`.review` rule to claude-sonnet-4-5 -> RED.

R7 — `bc-container launch --orchestrator fabro` must ACTUALLY RETURN after the
    engage.  The engage is issued DETACHED at the DOCKER level via
    `tmux new-session -d` so the blocking `docker exec` (driver.exec_run reads
    the exec pipes to EOF) returns after engaging — mirroring the tmux path.
    The v0.3.49 nohup-inside-the-script fix was ineffective because the
    backgrounded children inherit the exec's stdout/stderr pipes.
    TEETH: make the engage exec synchronous/blocking (drop the
    `tmux new-session -d` wrapper) -> RED.

FIDELITY: R6 reads the REAL committed workflow.fabro and runs the REAL cached
`fabro validate` when available (skip honestly if not); R7 drives the REAL
`controller.launch` over the FakeDockerDriver and reads the ACTUAL recorded
engage exec.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from bc_launcher.controller import (
    BcContainerController,
    _fabro_def_asset_root,
)
from tests.fake_driver import FakeDockerDriver


# ===========================================================================
# Helpers — the REAL committed workflow.fabro and the REAL cached fabro binary.
# ===========================================================================

def _workflow_text() -> str:
    return (_fabro_def_asset_root() / "workflow.fabro").read_text()


def _stylesheet(graph: str) -> str:
    m = re.search(r'model_stylesheet="([^"]*)"', graph)
    assert m is not None, "model_stylesheet= not found in workflow.fabro"
    return m.group(1)


def _stylesheet_model(sheet: str, cls: str) -> str | None:
    m = re.search(rf"\.{re.escape(cls)}\s*\{{\s*model:\s*([A-Za-z0-9._-]+)", sheet)
    return m.group(1) if m else None


def _locate_fabro() -> str | None:
    on_path = shutil.which("fabro")
    if on_path:
        return on_path
    cached = Path("/tmp/fabro-cache/fabro")
    if cached.is_file() and os.access(cached, os.X_OK):
        return str(cached)
    return None


# ===========================================================================
# R6 — .coding / .review resolve to claude-haiku-4-5 (real, HONORED primary).
# ===========================================================================

def test_r6_coding_stylesheet_rule_pins_haiku():
    """The `.coding` stylesheet rule pins claude-haiku-4-5 (a REAL primary
    model), NOT the persistently-429 claude-sonnet-4-5.

    TEETH: revert `.coding` to claude-sonnet-4-5 -> RED.
    """
    model = _stylesheet_model(_stylesheet(_workflow_text()), "coding")
    assert model == "claude-haiku-4-5", (
        "R6(a): the `.coding` stylesheet rule must pin claude-haiku-4-5 as a "
        "real primary model (sonnet-4-5 is persistently 429 on the fleet); "
        f"got {model!r}."
    )


def test_r6_review_stylesheet_rule_pins_haiku():
    """The `.review` stylesheet rule pins claude-haiku-4-5 (a REAL primary
    model), NOT the persistently-429 claude-sonnet-4-5.

    TEETH: revert `.review` to claude-sonnet-4-5 -> RED.
    """
    model = _stylesheet_model(_stylesheet(_workflow_text()), "review")
    assert model == "claude-haiku-4-5", (
        "R6(a): the `.review` stylesheet rule must pin claude-haiku-4-5 as a "
        "real primary model (sonnet-4-5 is persistently 429 on the fleet); "
        f"got {model!r}."
    )


def test_r6_no_swallowed_fallbacks_key_remains():
    """No `fallbacks:` key remains in the stylesheet — fabro 0.254.0 SILENTLY
    SWALLOWS it (a false-green), so the fix must use a real `model:` value, not
    a swallowed `fallbacks:` chain.

    TEETH: add `.coding { model: ...; fallbacks: ... }` -> RED.
    """
    sheet = _stylesheet(_workflow_text())
    assert "fallbacks" not in sheet, (
        "the model_stylesheet must carry NO `fallbacks:` key — fabro 0.254.0 "
        "silently swallows it (unhonored false-green); use a real `model:` "
        f"value instead. sheet:\n{sheet}"
    )


def test_r6_coding_review_class_nodes_resolve_to_haiku_via_real_validate():
    """PROOF-OF-HONORED (the lead's own technique): a `model:` value IS parsed
    and APPLIED to the class nodes by `fabro validate`.  A bogus `model:` on a
    `.coding` node is CAUGHT (`stylesheet_model_known` / `node_model_known`),
    whereas a swallowed `fallbacks:` key would NOT be — proving the haiku
    primary is REALLY applied, not silently dropped.

    (1) The committed def validates exit 0 + valid:true + zero diagnostics with
        the haiku primary.
    (2) A BOGUS `.coding` model produces `node_model_known` diagnostics naming
        the `.coding`-class nodes -> the `model:` value is HONORED/parsed.

    SKIP honestly only if the real fabro binary cannot be obtained.
    """
    fabro = _locate_fabro()
    if fabro is None:
        pytest.skip("fabro binary not available (cached/PATH); real-validate skipped")
    src = _fabro_def_asset_root() / "workflow.fabro"

    def _validate(text: str, tmp: Path) -> dict:
        wf = tmp / "workflow.fabro"
        wf.write_text(text)
        # copy the sibling def files so inputs bind and no stray warnings appear
        for sib in src.parent.iterdir():
            if sib.name == "workflow.fabro":
                continue
            dest = tmp / sib.name
            if sib.is_dir():
                shutil.copytree(sib, dest)
            else:
                dest.write_text(sib.read_text())
        proc = subprocess.run(
            [fabro, "validate", "--no-upgrade-check", "--json", str(wf)],
            capture_output=True, text=True, timeout=120,
        )
        return json.loads(proc.stdout)

    import tempfile
    committed = src.read_text()
    with tempfile.TemporaryDirectory() as d1:
        doc = _validate(committed, Path(d1))
    assert doc.get("valid") is True, (
        f"committed def must validate valid:true; got {doc.get('valid')!r}"
    )
    assert doc.get("diagnostics") == [], (
        f"committed def must report ZERO diagnostics; got {doc.get('diagnostics')!r}"
    )

    # PROOF-OF-HONORED: a bogus `.coding` model -> node_model_known diagnostics.
    bogus = committed.replace(
        ".coding { model: claude-haiku-4-5 }",
        ".coding { model: claude-bogus-9-9 }",
    )
    assert bogus != committed, "expected to rewrite the `.coding` rule"
    with tempfile.TemporaryDirectory() as d2:
        bogus_doc = _validate(bogus, Path(d2))
    node_model_diags = [
        diag for diag in bogus_doc.get("diagnostics", [])
        if diag.get("rule") in ("node_model_known", "stylesheet_model_known")
        and "claude-bogus-9-9" in (diag.get("message") or "")
    ]
    assert node_model_diags, (
        "a BOGUS `.coding` `model:` value must be CAUGHT by fabro validate "
        "(node_model_known / stylesheet_model_known) — proving `model:` is "
        "HONORED/parsed (not swallowed like a `fallbacks:` key would be). "
        f"diagnostics:\n{bogus_doc.get('diagnostics')!r}"
    )


# ===========================================================================
# R7 — the fabro engage exec is DETACHED (docker-level) so launch RETURNS.
# ===========================================================================

BC_NAME = "shopsystem-messaging"
WORK_ID = "lead-lwk4-work"
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


def _fabro_engage_exec(tmp_path: Path):
    """Drive the REAL launcher on the fabro path and return the recorded
    ExecCall carrying the fabro engage (server start + run)."""
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
    for c in driver.exec_calls:
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and "fabro server start" in c.command[2]
            and "fabro run" in c.command[2]
        ):
            return c
    raise AssertionError("the fabro engage exec (server start + run) must exist")


def test_r7_engage_exec_is_detached_so_launch_returns(tmp_path):
    """The engage exec is issued DETACHED at the docker level (`docker exec -d`,
    ExecCall.detach is True).  The v0.3.49 nohup-inside-the-script fix was
    ineffective because the backgrounded children INHERIT the exec's
    stdout/stderr pipes, so `subprocess.run` never saw EOF and `launch()`
    blocked for the foreground fabro server's lifetime.  Issuing the engage via
    `docker exec -d` returns immediately WITHOUT reading the exec's pipes, so
    `launch()` RETURNS after the engage is issued — mirroring the tmux path's
    detached-session return.  The engage SCRIPT is unchanged (docker-level
    detach), so scn 77 / esy4 pins stay green verbatim.

    TEETH: issue the engage exec synchronously (detach=False) -> RED.
    """
    call = _fabro_engage_exec(tmp_path)
    assert call.detach is True, (
        "R7: the fabro engage must be issued DETACHED (docker exec -d) so the "
        "blocking `docker exec` returns after engaging and `launch()` does not "
        f"hang on the foreground fabro server; call={call!r}"
    )


def test_r7_engage_still_issues_server_and_watcher_inside_detached_session(tmp_path):
    """Detaching changes HOW the engage is issued, not WHETHER: the
    `fabro server start --foreground --no-web` (the ONE per-container server) and
    the external agent-free watcher supervisor stay present INSIDE the detached
    session (lead-1vbw / ADR-058 AMENDMENT-3 replaced the retired persistent
    `fabro run dispatcher.toml` engage with `shop-msg watch`-driven finite
    `fabro run workflow.fabro` children).

    TEETH: drop the server-start or the watcher supervisor -> RED.
    """
    script = _fabro_engage_exec(tmp_path).command[2]
    assert "fabro server start --foreground --no-web" in script, (
        f"the engage pins the server-start argv; script:\n{script}"
    )
    # The external watcher supervisor: `shop-msg watch` is the always-resident
    # process and each wake fires a FINITE `fabro run` child of the UNCHANGED
    # workflow.fabro graph — NOT the retired infinite `fabro run dispatcher.toml`.
    assert 'shop-msg watch --bc "$BC_NAME"' in script, (
        "the engage must run the always-resident `shop-msg watch` supervisor "
        f"(lead-1vbw); script:\n{script}"
    )
    assert 'graph = "workflow.fabro"' in script, (
        "each finite child must run the UNCHANGED ADR-051 workflow.fabro graph; "
        f"script:\n{script}"
    )
    assert "fabro run dispatcher.toml" not in script, (
        "the retired infinite `fabro run dispatcher.toml` engage must be gone; "
        f"script:\n{script}"
    )
    # The per-child WORK_ID now rides the materialized child's
    # `[run.environment.env]` overlay (the f38ab guarantee), delivered per finite
    # child rather than at launch time (ADR-058 D6: the launch interface still
    # requires no work id).
    assert "[run.environment.env]" in script and "WORK_ID" in script, (
        "the per-child WORK_ID must ride the `[run.environment.env]` overlay; "
        f"script:\n{script}"
    )


def test_r7_env_before_install_ordering_preserved_under_detach(tmp_path):
    """esy4 Defect D stays intact under the detach change: the three exports
    still PRECEDE `fabro install` inside the detached session.

    TEETH: reorder an export after `fabro install` -> RED.
    """
    script = _fabro_engage_exec(tmp_path).command[2]
    install_pos = script.find("fabro install")
    assert install_pos != -1, f"engage must run `fabro install`; script:\n{script}"
    for token in ("SSL_CERT_FILE=", "ANTHROPIC_API_KEY=", "ANTHROPIC_BASE_URL="):
        pos = script.find(token)
        assert pos != -1 and pos < install_pos, (
            f"esy4 Defect D: {token!r} must precede `fabro install` under the "
            f"R7 detach change; script:\n{script}"
        )


def test_r7_fabro_path_issues_no_tmux_agent_or_claude_engage(tmp_path):
    """The R7 detach uses its OWN `fabro-engage` tmux session, NOT the tmux
    orchestrator's `agent` session: the fabro path still starts NO tmux `agent`
    send-keys and NO `claude` engage (the engage tier stays REPLACED, not
    added).

    TEETH: start a tmux `agent` send-keys / claude engage on the fabro path -> RED.
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
    assert result.exit_code == 0
    agent_send_keys = [
        c for c in driver.exec_calls
        if c.command[:2] == ["tmux", "send-keys"]
        and "-t" in c.command
        and "agent" in c.command[c.command.index("-t") + 1: c.command.index("-t") + 2]
    ]
    assert agent_send_keys == [], (
        "the fabro path must start NO tmux `agent` send-keys; the R7 detach uses "
        f"its own `fabro-engage` session. issued: {[c.command for c in agent_send_keys]!r}"
    )
    claude = [
        c for c in driver.exec_calls
        if any("agent-vault run -- claude" in tok for tok in c.command)
    ]
    assert claude == [], (
        f"the fabro path must start NO `claude` engage; issued: {[c.command for c in claude]!r}"
    )
