"""Functional teeth for lead-oqaw / scenario 32009f85a099be62 (@origin
lead-01jw.1) — the STARTUP INBOX DRAIN must fire one finite `fabro run` child
per PRE-EXISTING pending work_id, and EACH of those startup-drain children must
TARGET the ONE already-running shared per-container fabro server (never start
its own), so the resident fabro-server count stays EXACTLY 1 and no child fails
`Server already running (pid <n>)`.

WHY THIS IS A DISTINCT SCENARIO FROM THE MESSAGE-DRIVEN ONE (9f785e78ed55da4b):
the sibling pin (test_lead_oqaw_finite_run_shared_server) proves the finite-run
WORKER command (`run_finite`) targets the shared server.  It does NOT prove the
STARTUP-DRAIN TRIGGER PATH — the `drain` fired at engage start, BEFORE the
always-resident supervise loop — is wired to that shared-server worker.  A
container that comes up with two or more work_ids already pending
(`shop-msg pending inbox --bc <name>` non-empty before the watcher started)
relies on this startup drain to process them to terminal; if the startup drain
fired its OWN `fabro run` that autostarted a second server, every pre-existing
pending work_id would jam on `Server already running` and stay stuck pending.

THIS pins, structurally (dockerless, over the REAL recorded engage exec — same
fidelity discipline as the sibling pins):

  1. The engage composes a STARTUP drain — a bare `drain` call issued BEFORE the
     `while true` supervise loop — so pre-existing pending work_ids are swept at
     startup rather than only on the next inbound NOTIFY.
  2. That startup drain reaches the shared-server finite-run worker: `drain`
     dispatches (`dispatch`), and `dispatch` fires `run_finite`, whose sole
     `fabro run` command is DIRECTED at the shared server via
     `--server "$FABRO_SERVER"`.
  3. The resident fabro-server count stays EXACTLY 1: the ONLY `fabro server
     start` in the whole engage is the shared per-container bootstrap, and the
     finite-child worker composes none.

TEETH: revert `run_finite` to `fabro run "child-...toml" --auto-approve`
(FABRO_SERVER only ambiently exported) and this REDs — the startup-drain
children the scenario fires would then each autostart a second server.
"""
from __future__ import annotations

from pathlib import Path

from bc_launcher.controller import (
    BcContainerController,
    FABRO_WATCH_SERVER_SOCKET,
)
from tests.fake_driver import FakeDockerDriver


BC_NAME = "shopsystem-messaging"
WORK_ID = "lead-oqaw-startup-1"
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


def _run_finite_body(script: str) -> str:
    """The `run_finite` worker body — from `run_finite() {` up to the next
    worker definition (`dispatch() {`)."""
    start = script.find("run_finite() {")
    assert start != -1, f"the finite-child worker `run_finite` must exist; script:\n{script}"
    end = script.find("dispatch() {", start)
    assert end != -1, f"the `dispatch` worker must follow `run_finite`; script:\n{script}"
    return script[start:end]


# ---------------------------------------------------------------------------
# 32009f85a099be62 — the STARTUP drain fires per pre-existing pending work_id
# BEFORE the supervise loop, and reaches the shared-server finite-run worker.
# ---------------------------------------------------------------------------

def test_startup_drain_is_composed_before_the_supervise_loop(tmp_path):
    """The engage must fire a STARTUP drain — a bare `drain` call — BEFORE the
    always-resident `while true` supervise loop, so two-or-more work_ids that
    were already pending when the watcher started are swept at startup (one
    finite child per pre-existing pending work_id) rather than waiting for the
    next inbound NOTIFY.  The startup drain reaches the finite worker via
    `drain` -> `dispatch` -> `run_finite`."""
    script = _engage_script(tmp_path)
    lines = script.splitlines()

    supervise_idx = next(
        (i for i, ln in enumerate(lines) if "while true" in ln), None
    )
    assert supervise_idx is not None, (
        f"the always-resident `while true` supervise loop must exist; script:\n{script}"
    )
    # A STARTUP drain: a standalone `drain` invocation (not the `drain() {`
    # definition, not a comment) positioned before the supervise loop.
    startup_drain_idx = next(
        (
            i for i, ln in enumerate(lines)
            if ln.strip() == "drain" and i < supervise_idx
        ),
        None,
    )
    assert startup_drain_idx is not None, (
        "the engage must issue a STARTUP `drain` (bare `drain` call) BEFORE the "
        "`while true` supervise loop, so pre-existing pending work_ids are swept "
        f"at startup; script:\n{script}"
    )
    # The startup drain reaches the finite worker: drain -> dispatch -> run_finite.
    drain_start = script.find("drain() {")
    drain_end = script.find("drain\n", drain_start)  # first bare `drain` after def
    drain_body = script[drain_start:drain_end]
    assert "dispatch" in drain_body, (
        "the `drain` worker must dispatch each pending work_id "
        f"(drain -> dispatch); drain body:\n{drain_body}"
    )
    dispatch_start = script.find("dispatch() {")
    dispatch_end = script.find("drain() {", dispatch_start)
    dispatch_body = script[dispatch_start:dispatch_end]
    assert "run_finite" in dispatch_body, (
        "the `dispatch` worker must fire the shared-server finite worker "
        f"(dispatch -> run_finite); dispatch body:\n{dispatch_body}"
    )


def test_startup_drain_finite_children_target_the_shared_server(tmp_path):
    """EACH startup-drain finite child must be DIRECTED at the ONE already-running
    shared server: the finite worker `run_finite` issues its `fabro run` command
    with `--server "$FABRO_SERVER"`, so a startup drain that fires N>=2 children
    (one per pre-existing pending work_id) attaches every one of them to the
    single shared server instead of autostarting a second (which would fail
    `Server already running (pid <n>)`)."""
    script = _engage_script(tmp_path)
    run_finite_body = _run_finite_body(script)

    fabro_run_lines = [
        ln for ln in run_finite_body.splitlines()
        if ln.strip().startswith("fabro run ")
    ]
    assert fabro_run_lines, (
        "the finite worker `run_finite` must issue a `fabro run` child command; "
        f"run_finite body:\n{run_finite_body}"
    )
    for ln in fabro_run_lines:
        assert '--server "$FABRO_SERVER"' in ln, (
            "each startup-drain finite child's `fabro run` command must be "
            'DIRECTED at the shared server via --server "$FABRO_SERVER" so it '
            "attaches to the ONE shared server instead of autostarting its own; "
            f"offending line:\n  {ln}\nrun_finite body:\n{run_finite_body}"
        )


def test_startup_drain_never_brings_up_a_second_server(tmp_path):
    """The resident fabro-server count stays EXACTLY 1 across the startup drain:
    the ONLY `fabro server start` in the whole engage is the shared per-container
    bootstrap, and the finite-child worker composes none — so no startup-drain
    child can bring up a second server."""
    script = _engage_script(tmp_path)

    assert script.count("fabro server start") == 1, (
        "EXACTLY ONE `fabro server start` (the shared per-container server) may "
        f"appear; count={script.count('fabro server start')}; script:\n{script}"
    )
    run_finite_body = _run_finite_body(script)
    assert "fabro server start" not in run_finite_body, (
        "the finite-child worker must NOT start a server — each startup-drain "
        "child targets the ONE shared server; run_finite body:\n" + run_finite_body
    )
    assert FABRO_WATCH_SERVER_SOCKET in script, (
        "the one shared server must bind the container-scoped socket "
        f"{FABRO_WATCH_SERVER_SOCKET}; script:\n{script}"
    )
