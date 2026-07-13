"""Functional teeth for lead-oqaw / scenario 9f785e78ed55da4b — each
message-driven finite `fabro run` child must TARGET the ONE already-running
shared per-container fabro server, NOT start its own.

ROOT CAUSE (v0.3.67 prod defect, lead-01jw.1): the engage starts EXACTLY ONE
shared per-container fabro server, but each inbound-message finite
`fabro run workflow.fabro` child then tries to START ITS OWN server, so fabro
refuses `x Failed to start fabro server ... Server already running (pid <n>)`;
the children exit 1 (non-fatal), emit NO work_done, and the dispatches stay
stuck pending.  The fleet is leak-free but cannot process work.

WHY THE PRIOR PIN MISSED IT (728871aca27b0d8f, stub-level): the sibling pin
only asserted the STRING `FABRO_SERVER=<sock>` appears somewhere in the script
(i.e. the env var is *assigned*).  It never asserted the finite-child
`fabro run` command actually CONSUMES that server target — so a child that
merely inherits the exported env, then autostarts its own server because the
run command is not directed at the shared one, sailed through green.

THIS pins the FUNCTIONAL outcome the scenario requires: the finite-child
`fabro run` invocation is directed at the shared server via `$FABRO_SERVER`, so
each of N>=2 finite children attaches to the ONE already-running shared server
rather than starting a second one.  Structurally (dockerless, over the REAL
recorded engage exec — same fidelity discipline as test_lead_1vbw_watch_engage):
the child-run command carries the shared-server target, and NO `fabro server
start` lives on the finite-child path (the resident-server count stays 1).

TEETH: revert `run_finite` to `fabro run "child-...toml" --auto-approve` with
FABRO_SERVER only ambiently exported -> the invocation no longer targets the
shared server and this REDs.
"""
from __future__ import annotations

from pathlib import Path

from bc_launcher.controller import (
    BcContainerController,
    FABRO_WATCH_SERVER_SOCKET,
)
from tests.fake_driver import FakeDockerDriver


BC_NAME = "shopsystem-messaging"
WORK_ID = "lead-oqaw-work-1"
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


def _engage_script(tmp_path: Path) -> str:
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
        ):
            return c.command[2]
    raise AssertionError("the fabro engage exec (server start) must exist")


def _finite_run_invocations(script: str) -> list[str]:
    """The finite-child `fabro run` COMMAND lines (not comments, not the child
    config's `graph = "workflow.fabro"` line)."""
    return [
        line for line in script.splitlines()
        if line.strip().startswith("fabro run ")
    ]


# ---------------------------------------------------------------------------
# 9f785e78ed55da4b — each finite child targets the ONE shared server; no child
# starts a second server, so none fails "Server already running".
# ---------------------------------------------------------------------------

def test_finite_run_child_is_directed_at_the_shared_fabro_server(tmp_path):
    """Each message-driven finite `fabro run` child must be DIRECTED at the ONE
    already-running shared server via `$FABRO_SERVER` — not merely inherit the
    exported env while autostarting its own server.  This is the functional
    outcome scenario 9f785e78ed55da4b pins: with the shared server already up,
    N>=2 finite children attach to it rather than each starting a second one."""
    script = _engage_script(tmp_path)

    invocations = _finite_run_invocations(script)
    assert invocations, (
        "the watcher must fire a finite `fabro run` child command; "
        f"script:\n{script}"
    )
    for inv in invocations:
        assert "$FABRO_SERVER" in inv, (
            "each finite-child `fabro run` invocation must be DIRECTED at the "
            "already-running shared server via $FABRO_SERVER so it attaches to "
            "the ONE shared server instead of autostarting its own (which fails "
            "'Server already running (pid <n>)'); offending invocation:\n"
            f"  {inv}\nscript:\n{script}"
        )


def test_no_second_server_start_on_the_finite_child_path(tmp_path):
    """The resident fabro-server count stays EXACTLY 1: the ONLY `fabro server
    start` in the whole engage is the shared per-container bootstrap, and NO
    `fabro server start` is composed onto the per-child finite-run path
    (`run_finite`).  So a finite child can never bring up a second server."""
    script = _engage_script(tmp_path)

    assert script.count("fabro server start") == 1, (
        "EXACTLY ONE `fabro server start` (the shared per-container server) may "
        f"appear; count={script.count('fabro server start')}; script:\n{script}"
    )
    # The finite-child worker (run_finite) must contain no server start.
    start = script.find("run_finite() {")
    assert start != -1, f"the finite-child worker `run_finite` must exist; script:\n{script}"
    # dispatch() is defined immediately after run_finite; bound the body there.
    end = script.find("dispatch() {", start)
    assert end != -1, f"the `dispatch` worker must follow `run_finite`; script:\n{script}"
    run_finite_body = script[start:end]
    assert "fabro server start" not in run_finite_body, (
        "the per-child finite-run worker must NOT start a server — each child "
        "targets the ONE shared server; run_finite body:\n" + run_finite_body
    )
    assert FABRO_WATCH_SERVER_SOCKET in script, (
        "the one shared server must bind the container-scoped socket "
        f"{FABRO_WATCH_SERVER_SOCKET}; script:\n{script}"
    )
