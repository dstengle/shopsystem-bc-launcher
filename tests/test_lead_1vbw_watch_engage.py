"""Structural teeth for lead-1vbw — the EXTERNAL agent-free message-driven
watcher supervisor engage (replaces the infinite `fabro run dispatcher.toml`).

ROOT CAUSE (lead-01jw, P0): the prior `--orchestrator fabro` engage ran ONE
never-ending `fabro run dispatcher.toml` cyclic poll-loop whose per-tick
run-graph events accumulated UNBOUNDED in the fabro server heap (RSS 18->28GiB
during PURE idle polling, OOM-bound), and it maintained NO shop-msg heartbeat
(lead-8hpz: live-but-offline).

THE FIX (7 pinned scenarios + product-authority directive): the engage becomes
an EXTERNAL, agent-free, message-driven watcher supervisor.  Its ONLY
always-resident process is `shop-msg watch --bc <name>` (LISTEN/NOTIFY event
source + bc_presence heartbeat).  Each inbound message fires ONE FINITE
`fabro run workflow.fabro` child against EXACTLY ONE long-lived per-container
fabro server (NOT one ephemeral server per run), and the supervisor publishes a
scrapeable telemetry surface (server resident memory + active/completed run
counts).  Idle => zero resident runs.

FIDELITY: the assertions bind to the REAL launcher's ACTUAL recorded engage exec
over the FakeDockerDriver (controller.launch(launch_path="fabro")), never a
model.  Dockerless: validated structurally over the recorded `/bin/sh -c`
engage script, not by observing a live server.

TEETH: revert `_fabro_engage_script` to the infinite `fabro run dispatcher.toml`
engage -> every assertion below REDs.
"""
from __future__ import annotations

import re
from pathlib import Path

from bc_launcher.controller import (
    BcContainerController,
    FABRO_WATCH_SERVER_SOCKET,
    FABRO_WATCH_TELEMETRY_FILE,
)
from tests.fake_driver import FakeDockerDriver


BC_NAME = "shopsystem-messaging"
WORK_ID = "lead-1vbw-work-7"
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


# ---------------------------------------------------------------------------
# 47da82f60bbd47a9 — external watcher; NO `fabro run dispatcher.toml`; zero idle.
# ---------------------------------------------------------------------------

def test_engage_always_resident_is_shop_msg_watch(tmp_path):
    """The ONLY always-resident process is `shop-msg watch --bc <name>`, bound to
    this BC (BC_NAME set to the launch's bc_name)."""
    script = _engage_script(tmp_path)
    assert f"BC_NAME='{BC_NAME}'" in script or f"BC_NAME={BC_NAME}" in script, (
        f"the watcher must bind BC_NAME to {BC_NAME}; script:\n{script}"
    )
    assert 'shop-msg watch --bc "$BC_NAME"' in script, (
        "the watcher engage must run `shop-msg watch --bc \"$BC_NAME\"` as its "
        f"always-resident process; script:\n{script}"
    )


def test_engage_fires_finite_workflow_child_not_dispatcher(tmp_path):
    """Each inbound message fires ONE finite child that runs the UNCHANGED
    ADR-051 `workflow.fabro` graph (via a per-child config, byte-identical to the
    reference's materialize_child); the engage runs NO long-lived `fabro run
    dispatcher.toml`/`.fabro`, and the per-child WORK_ID rides `[run.environment.env]`
    (the f38ab guarantee, preserved)."""
    script = _engage_script(tmp_path)
    assert "fabro run" in script, (
        f"the watcher must fire finite `fabro run` children; script:\n{script}"
    )
    assert 'graph = "workflow.fabro"' in script, (
        "the finite child must bind the UNCHANGED ADR-051 workflow.fabro graph; "
        f"script:\n{script}"
    )
    assert "[run.environment.env]" in script and "WORK_ID" in script, (
        "the per-child WORK_ID must ride the `[run.environment.env]` overlay "
        f"(f38ab guarantee preserved); script:\n{script}"
    )
    assert "fabro run dispatcher.toml" not in script, (
        "the engage must NOT run the infinite `fabro run dispatcher.toml`; "
        f"script:\n{script}"
    )
    assert "fabro run dispatcher.fabro" not in script, (
        "the engage must NOT run the bare `fabro run dispatcher.fabro`; "
        f"script:\n{script}"
    )


# ---------------------------------------------------------------------------
# 728871aca27b0d8f — EXACTLY ONE long-lived per-container server (not per-run).
# ---------------------------------------------------------------------------

def test_engage_starts_exactly_one_server_bound_to_container_socket(tmp_path):
    """EXACTLY ONE `fabro server start`, bound to the container-scoped socket;
    it is NOT started per run and NOT killed per run (the reference workaround)."""
    script = _engage_script(tmp_path)
    assert script.count("fabro server start") == 1, (
        "the engage must start EXACTLY ONE per-container fabro server (not one "
        f"ephemeral server per run); count={script.count('fabro server start')}; "
        f"script:\n{script}"
    )
    assert FABRO_WATCH_SERVER_SOCKET in script, (
        "the one server must bind the container-scoped socket "
        f"{FABRO_WATCH_SERVER_SOCKET}; script:\n{script}"
    )
    assert f"FABRO_SERVER={FABRO_WATCH_SERVER_SOCKET}" in script, (
        "each finite child must target the ONE shared server via "
        f"FABRO_SERVER={FABRO_WATCH_SERVER_SOCKET}; script:\n{script}"
    )


# ---------------------------------------------------------------------------
# edc035fdde4062df — scrapeable telemetry surface (RSS + run counts).
# ---------------------------------------------------------------------------

def test_engage_publishes_telemetry_surface(tmp_path):
    """The supervisor publishes a scrapeable telemetry surface: the server's
    resident memory + active and completed finite-run counts."""
    script = _engage_script(tmp_path)
    assert FABRO_WATCH_TELEMETRY_FILE in script, (
        "the watcher must publish a scrapeable telemetry file "
        f"{FABRO_WATCH_TELEMETRY_FILE}; script:\n{script}"
    )
    assert "VmRSS" in script or "resident_memory" in script, (
        "telemetry must sample the server's resident memory; script:\n{script}"
    )
    for token in ("active_runs", "completed_runs"):
        assert token in script, (
            f"telemetry must publish {token}; script:\n{script}"
        )


# ---------------------------------------------------------------------------
# e94a01b26ed6a4cc — heartbeat via shop-msg watch (bc_presence).
# ---------------------------------------------------------------------------

def test_engage_heartbeat_is_shop_msg_watch(tmp_path):
    """The bc_presence heartbeat is maintained by the always-resident
    `shop-msg watch` process (it is the sole heartbeat source), and NO infinite
    `fabro run dispatcher.toml` remains (which maintained no heartbeat)."""
    script = _engage_script(tmp_path)
    assert 'shop-msg watch --bc "$BC_NAME"' in script
    assert "fabro run dispatcher.toml" not in script


# ---------------------------------------------------------------------------
# 7a4f7eed52594107 — agent-free path; failed finite child is non-fatal.
# ---------------------------------------------------------------------------

def test_engage_is_agent_free(tmp_path):
    """No claude / LLM / model-backed agent anywhere in the watcher dispatch
    path: the always-resident process is `shop-msg watch` and each dispatch fires
    a finite native `fabro run workflow.fabro` child."""
    script = _engage_script(tmp_path)
    assert "claude" not in script, (
        f"the watcher dispatch path must be agent-free (no claude); script:\n{script}"
    )


def test_engage_finite_child_is_non_fatal(tmp_path):
    """A finite child that exits non-zero is swallowed as NON-FATAL — its
    in-flight lock is released and the watcher keeps serving.  Structurally: the
    child is fired in a backgrounded, lock-releasing worker, not in a
    fail-the-supervisor foreground `&&` chain."""
    script = _engage_script(tmp_path)
    # The child run is dispatched in a backgrounded worker (dedup-guarded), so a
    # non-zero child never propagates to the always-resident supervisor.
    assert re.search(r"run_finite\b", script), (
        "the finite child must run in an isolated `run_finite` worker so its "
        f"failure is non-fatal to the supervisor; script:\n{script}"
    )
    # The in-flight lock is released for the child regardless of outcome.
    assert "rm -rf" in script and "inflight" in script, (
        "a finished/failed child must release its in-flight lock; "
        f"script:\n{script}"
    )


# ---------------------------------------------------------------------------
# 9d737bcd0f4473e9 — startup drain + in-flight dedup.
# ---------------------------------------------------------------------------

def test_engage_startup_drains_pending_inbox(tmp_path):
    """On startup the watcher DRAINS the pre-existing pending inbox, the
    authoritative pending set being `shop-msg pending inbox --bc <name>`."""
    script = _engage_script(tmp_path)
    assert 'shop-msg pending inbox --bc "$BC_NAME"' in script, (
        "startup drain must query `shop-msg pending inbox --bc \"$BC_NAME\"`; "
        f"script:\n{script}"
    )


def test_engage_inflight_dedup_atomic_mkdir_lock(tmp_path):
    """While a child for W is in flight a second wake for W is SKIPPED by
    in-flight dedup — an atomic `mkdir` lock per work_id."""
    script = _engage_script(tmp_path)
    assert re.search(r"mkdir\s+.*inflight", script), (
        "in-flight dedup must be an atomic `mkdir` lock per work_id; "
        f"script:\n{script}"
    )


# ---------------------------------------------------------------------------
# UNCHANGED invariants preserved (esy4 / ze4w / lwk4): the server-config
# bootstrap + env-before-install + cd-first + one-server-brace-group.
# ---------------------------------------------------------------------------

def test_engage_preserves_env_before_install_and_cd_first(tmp_path):
    """The clone-path server-config bootstrap the watcher STILL NEEDS: `fabro
    install` precedes `fabro server start`, the three exports precede `fabro
    install`, and the script `cd`s into the def dir FIRST (so `fabro run`
    children resolve the poured workflow.fabro)."""
    script = _engage_script(tmp_path)
    assert script.lstrip().startswith("cd /workspace/.fabro &&"), (
        f"the engage must `cd /workspace/.fabro` first; script:\n{script}"
    )
    install_pos = script.find("fabro install")
    start_pos = script.find("fabro server start")
    assert install_pos != -1 and install_pos < start_pos, (
        "`fabro install` (server bootstrap) must precede `fabro server start`; "
        f"script:\n{script}"
    )
    for token in ("SSL_CERT_FILE=", "ANTHROPIC_API_KEY=", "ANTHROPIC_BASE_URL="):
        pos = script.find(token)
        assert pos != -1 and pos < install_pos, (
            f"esy4 Defect D: {token!r} must precede `fabro install`; script:\n{script}"
        )
