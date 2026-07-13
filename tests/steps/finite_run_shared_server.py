"""Step definitions + executable teeth for the watcher engage's finite runs
targeting the ONE shared per-container fabro server via ``$FABRO_SERVER``
(scenarios 9f785e78ed55da4b message-driven + 32009f85a099be62 startup-drain,
@origin:lead-01jw.1, work_id lead-oqaw).

v0.3.67 prod defect: the engage started EXACTLY ONE shared per-container fabro
server, but each finite ``fabro run workflow.fabro`` child then tried to START
ITS OWN server; fabro refused with "Server already running (pid <n>)", the
children exited 1 with NO work_done, and their dispatches stuck pending. The one
fix — the finite child attaches to the shared server via the already-exported
``$FABRO_SERVER`` (``fabro run --server "$FABRO_SERVER" ...``) — routes through
the SINGLE ``run_finite`` worker, fixing BOTH the message-driven watcher path
and the startup-drain path at once.

FIDELITY: these steps bind to the REAL launcher's ACTUAL recorded
``--orchestrator fabro`` engage script (controller.launch over the
FakeDockerDriver, exactly like tests/test_lead_1vbw_watch_engage.py) and
EXECUTE the recorded finite-run invocation against a ``fabro`` stub that
faithfully models fabro's server-already-running refusal (real teeth, no live
container/server). Dockerless.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pytest_bdd import given, when, then

from bc_launcher.controller import (
    BcContainerController,
    FABRO_WATCH_SERVER_SOCKET,
)
from tests.fake_driver import FakeDockerDriver

_BC_NAME = "shopsystem-messaging"
_WORK_ID = "lead-oqaw-work-1"
_HOST_TREE = "/host/live/shopsystem-messaging"


def _make_credential_home(tmp_path: Path) -> Path:
    home = tmp_path / "fake_home"
    home.mkdir(exist_ok=True)
    (home / ".claude").mkdir(exist_ok=True)
    (home / ".claude" / ".claude.json").write_text("{}")
    (home / ".config" / "gh").mkdir(parents=True, exist_ok=True)
    (home / ".gitconfig").write_text("")
    return home


def _make_manifest(tmp_path: Path) -> Path:
    manifest = tmp_path / "bc-manifest.yaml"
    manifest.write_text(
        "product: shopsystem product\n"
        "bcs:\n"
        f"  - name: {_BC_NAME}\n"
        f"    remote: https://github.com/shopsystem/{_BC_NAME}.git\n"
        "    role: bc\n"
    )
    return manifest


def build_engage_script(tmp_path: Path) -> str:
    """Return the REAL launcher's recorded ``--orchestrator fabro`` engage
    exec (the ``/bin/sh -c`` script that starts the one shared server)."""
    driver = FakeDockerDriver()
    driver.set_host_tree_snapshot(
        _HOST_TREE,
        beads_registry='{"id":"seed-1","title":"committed"}\n',
        claude_skills="poured-skill-group/bc-router-health\n",
    )
    controller = BcContainerController(driver)
    result = controller.launch(
        bc_name=_BC_NAME,
        repo_url=None,
        workspace_mount=_HOST_TREE,
        launch_path="fabro",
        work_id=_WORK_ID,
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


def count_fabro_run_invocations(script: str) -> int:
    """Count EXECUTABLE ``fabro run`` invocations (excluding comment lines that
    merely mention it) — proves both paths share the single ``run_finite``."""
    return sum(
        1
        for ln in script.splitlines()
        if "fabro run" in ln and not ln.lstrip().startswith("#")
    )


def extract_run_finite_cmd(script: str) -> str:
    """Extract the finite-run invocation issued by ``run_finite`` — the
    ``fabro run ... --auto-approve`` command line, stripped of its redirection.

    This is the ONE place the engage issues ``fabro run`` (both the
    message-driven watcher path and the startup-drain path route through the
    single ``run_finite``)."""
    m = re.search(r"fabro run\b.*?--auto-approve", script)
    assert m, f"no `fabro run ... --auto-approve` in the engage; script:\n{script}"
    return m.group(0)


# A `fabro` shell stub that faithfully models fabro's server lifecycle:
#   * `fabro server start`         -> registers a resident server socket.
#   * `fabro run --server <sock>`  -> ATTACHES to the already-running shared
#                                     server and drives the run to a work_done.
#   * `fabro run` WITHOUT --server -> the child naively tries to start its OWN
#                                     server; the guard sees one already running
#                                     and refuses "Server already running
#                                     (pid <n>)", the run exits 1, NO work_done.
_FABRO_STUB = r'''
_servers="$SERVERS_FILE"
_outbox="$OUTBOX_FILE"
fabro() {
  if [ "$1" = "server" ] && [ "$2" = "start" ]; then
    echo "127.0.0.1:32276" >> "$_servers"
    return 0
  fi
  if [ "$1" = "run" ]; then
    _srv=""; _prev=""
    for _a in "$@"; do
      if [ "$_prev" = "--server" ]; then _srv="$_a"; fi
      _prev="$_a"
    done
    if [ -n "$_srv" ]; then
      # attach to the ONE shared server -> drive workflow to a work_done.
      printf 'work_done\n' > "$_outbox"
      return 0
    fi
    # no --server: naive self-start hits fabro's already-running guard.
    if [ -s "$_servers" ]; then
      echo "x Failed to start fabro server ... Server already running (pid 791) on 127.0.0.1:32276" >&2
      return 1
    fi
    echo "127.0.0.1:32276" >> "$_servers"
    printf 'work_done\n' > "$_outbox"
    return 0
  fi
  return 0
}
'''


def run_finite_children(script: str, work_ids, tmp_path: Path) -> dict:
    """EXECUTE the recorded finite-run invocation once per work_id against the
    fabro stub, with the ONE shared server ALREADY running. Returns per-child
    results plus the final resident-server count."""
    cmd = extract_run_finite_cmd(script)
    servers = tmp_path / "servers"
    # The GIVEN: EXACTLY ONE long-lived shared server is already running.
    servers.write_text("127.0.0.1:32276\n")

    results = {}
    for wid in work_ids:
        slug = re.sub(r"[^A-Za-z0-9._-]", "_", wid)
        outbox = tmp_path / f"outbox-{slug}"
        prog = (
            "set -u\n"
            f'SERVERS_FILE={servers}\n'
            f'OUTBOX_FILE={outbox}\n'
            f'FABRO_SERVER={FABRO_WATCH_SERVER_SOCKET}\n'
            "export FABRO_SERVER\n"
            f'_rf_sw={slug}\n'
            + _FABRO_STUB
            + "\n" + cmd + "\n"
        )
        proc = subprocess.run(
            ["bash", "-c", prog], capture_output=True, text=True
        )
        results[wid] = {
            "rc": proc.returncode,
            "stderr": proc.stderr,
            "work_done": outbox.read_text().strip() if outbox.exists() else "",
        }
    return {
        "children": results,
        "server_count": len(
            [ln for ln in servers.read_text().splitlines() if ln.strip()]
        ),
    }


def _assert_all_children_succeed(ctx):
    run = ctx["finite_run"]
    for wid, r in run["children"].items():
        assert r["rc"] == 0, (
            f"finite child {wid} must run SUCCESSFULLY against the already-"
            f"running shared server, not fail starting its own; exit {r['rc']}: "
            f"{r['stderr']!r} (lead-oqaw)"
        )
        assert "Server already running" not in r["stderr"], (
            f"child {wid} must NOT fail with fabro's 'Server already running "
            f"(pid <n>)' refusal; stderr={r['stderr']!r} (lead-oqaw)"
        )
    assert run["server_count"] == 1, (
        "the count of resident fabro servers must stay EXACTLY 1 throughout; "
        f"got {run['server_count']} (lead-oqaw)"
    )


def _assert_all_children_work_done(ctx):
    run = ctx["finite_run"]
    for wid, r in run["children"].items():
        assert r["work_done"] == "work_done", (
            f"finite child {wid} must reach a Reviewer-gated work_done on its "
            f"scenario path, not exit 1 with no work_done; got "
            f"{r['work_done']!r} (lead-oqaw)"
        )


def _assert_none_stuck_pending(ctx):
    run = ctx["finite_run"]
    stuck = [wid for wid, r in run["children"].items() if r["work_done"] != "work_done"]
    assert not stuck, (
        "every dispatched work_id must have a corresponding work_done and NONE "
        f"may remain stuck pending; stuck={stuck} (lead-oqaw)"
    )


# ---------------------------------------------------------------------------
# Scenario 9f785e78ed55da4b — message-driven finite runs.
# ---------------------------------------------------------------------------

@given('the container "bc-shopsystem-messaging" is running the "--orchestrator '
       'fabro" watcher engage with EXACTLY ONE long-lived shared per-container '
       'fabro server already started')
def given_msg_engage_one_server(ctx, tmp_path):
    script = build_engage_script(tmp_path)
    ctx["engage_script"] = script
    ctx["tmp_path"] = tmp_path
    assert script.count("fabro server start") == 1, (
        "the engage must start EXACTLY ONE per-container fabro server; count="
        f"{script.count('fabro server start')} (lead-oqaw)"
    )


@given('two or more inbound messages, each carrying a distinct work_id on a '
       'scenario path, are delivered to the BC inbox so the watcher fires one '
       'finite "fabro run workflow.fabro" child per message')
def given_two_messages(ctx):
    # Message-driven wakes route `shop-msg watch` -> dispatch -> run_finite.
    script = ctx["engage_script"]
    assert 'shop-msg watch --bc "$BC_NAME"' in script
    assert re.search(r"dispatch\s+.*_w_wid", script) or "dispatch \"$_w_wid\"" in script, (
        "each inbound wake must route through `dispatch` -> `run_finite`; "
        f"script:\n{script}"
    )
    ctx["work_ids"] = ["lead-mfnt", "lead-5oih", "lead-4mzu"]


@when("the finite children run")
def when_finite_children_run(ctx):
    ctx["finite_run"] = run_finite_children(
        ctx["engage_script"], ctx["work_ids"], ctx["tmp_path"]
    )


@then('EACH finite child runs SUCCESSFULLY against the already-running shared '
      'server rather than attempting to start its own server, so NO child '
      'fails with "Server already running (pid <n>)" and the count of resident '
      'fabro servers stays EXACTLY 1 throughout')
def then_msg_children_succeed(ctx):
    _assert_all_children_succeed(ctx)


@then('EACH finite child reaches its terminal by driving the workflow to a '
      'Reviewer-gated "work_done" emitted on that message\'s scenario path, '
      'rather than exiting 1 with no work_done')
def then_msg_children_work_done(ctx):
    _assert_all_children_work_done(ctx)


@then('after the finite runs complete every dispatched work_id has a '
      'corresponding "work_done" in the BC outbox and NONE of those dispatches '
      'remains stuck pending in the BC inbox')
def then_msg_none_stuck(ctx):
    _assert_none_stuck_pending(ctx)


# ---------------------------------------------------------------------------
# Scenario 32009f85a099be62 — startup inbox drain.
# ---------------------------------------------------------------------------

@given('the container "bc-shopsystem-messaging" starts the "--orchestrator '
       'fabro" watcher engage with its single long-lived shared per-container '
       'fabro server')
def given_drain_engage_one_server(ctx, tmp_path):
    script = build_engage_script(tmp_path)
    ctx["engage_script"] = script
    ctx["tmp_path"] = tmp_path
    assert script.count("fabro server start") == 1, (
        "the engage must start EXACTLY ONE per-container fabro server; count="
        f"{script.count('fabro server start')} (lead-oqaw)"
    )


@given('"shop-msg pending inbox --bc shopsystem-messaging" already lists two or '
       'more work_ids that arrived before the watcher started, so the startup '
       'drain fires one finite "fabro run workflow.fabro" child per pending '
       'work_id')
def given_startup_drain_pending(ctx):
    script = ctx["engage_script"]
    # The startup drain routes through the SAME shared run_finite: the drain
    # queries the authoritative pending set, then dispatch -> run_finite.
    assert 'shop-msg pending inbox --bc "$BC_NAME"' in script, (
        "startup drain must query `shop-msg pending inbox --bc \"$BC_NAME\"`; "
        f"script:\n{script}"
    )
    assert re.search(r"drain\s*\(\)\s*\{", script), "drain() must be defined"
    assert "dispatch \"$_dr_wid\"" in script, (
        "the drain must route each pending work_id through `dispatch` -> "
        f"`run_finite`; script:\n{script}"
    )
    # exactly ONE executable `fabro run` in the whole engage: both paths share
    # the single run_finite (comment mentions are excluded).
    n = count_fabro_run_invocations(script)
    assert n == 1, (
        "the message-driven path and the startup-drain path must share the "
        f"single `run_finite` `fabro run` invocation; count={n} (lead-oqaw)"
    )
    ctx["work_ids"] = ["lead-pre1", "lead-pre2", "lead-pre3"]


@when("the startup drain runs its finite children")
def when_startup_drain_runs(ctx):
    ctx["finite_run"] = run_finite_children(
        ctx["engage_script"], ctx["work_ids"], ctx["tmp_path"]
    )


@then('EACH drained finite child runs SUCCESSFULLY against the single shared '
      'server with the resident fabro-server count staying EXACTLY 1 and NO '
      'child failing with "Server already running (pid <n>)"')
def then_drain_children_succeed(ctx):
    _assert_all_children_succeed(ctx)


@then('EACH drained finite child reaches a Reviewer-gated "work_done" on its '
      'scenario path, so every pre-existing pending work_id is processed to '
      'terminal rather than left stuck pending')
def then_drain_children_work_done(ctx):
    _assert_all_children_work_done(ctx)


@then('once the drain completes the pending-inbox set for those drained '
      'work_ids is empty because each produced a terminal "work_done"')
def then_drain_none_stuck(ctx):
    _assert_none_stuck_pending(ctx)
