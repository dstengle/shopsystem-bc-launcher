"""pytest-bdd binding + structural teeth for the watcher engage's finite runs
targeting the ONE shared per-container fabro server via ``$FABRO_SERVER``
(scenarios 9f785e78ed55da4b message-driven + 32009f85a099be62 startup-drain,
@origin:lead-01jw.1, work_id lead-oqaw).

FUNCTIONAL-SUCCESS sharpening of the structurally-pinned one-per-container
watcher engage (728871aca27b0d8f + dc9a29a746921a14): the v0.3.67 engage
started EXACTLY ONE shared server but each finite ``fabro run`` child then tried
to start its OWN server and fabro refused "Server already running (pid <n>)",
so the children exited 1 with NO work_done and stuck pending. The ONE fix —
``fabro run --server "$FABRO_SERVER" ...`` inside the single ``run_finite``
worker — fixes BOTH the message-driven and startup-drain paths at once.

The two gherkin scenarios are bound (and genuinely EXECUTED against a fabro
stub that models the "Server already running" refusal) via
tests/steps/finite_run_shared_server.py. This module adds the structural teeth
over the REAL recorded engage script.
"""
from __future__ import annotations

from pytest_bdd import scenarios

from bc_launcher.controller import FABRO_WATCH_SERVER_SOCKET
from tests.steps.finite_run_shared_server import (
    build_engage_script,
    count_fabro_run_invocations,
    extract_run_finite_cmd,
)

scenarios("../features/bc_container_fabro_finite_run_shared_server.feature")


def test_finite_run_targets_shared_server_via_fabro_server_flag(tmp_path):
    """The single ``run_finite`` ``fabro run`` invocation must attach to the ONE
    shared server via the already-exported ``$FABRO_SERVER`` (``--server
    "$FABRO_SERVER"``) rather than letting the child start its own server —
    the fix for the v0.3.67 'Server already running' defect (lead-oqaw)."""
    script = build_engage_script(tmp_path)
    cmd = extract_run_finite_cmd(script)
    assert '--server "$FABRO_SERVER"' in cmd, (
        "the finite child must target the ONE shared server via `--server "
        '"$FABRO_SERVER"`, not start its own (v0.3.67: fabro refused "Server '
        f"already running (pid <n>)\"); run_finite cmd was: {cmd!r} (lead-oqaw)"
    )


def test_engage_starts_exactly_one_fabro_server(tmp_path):
    """Negative control: the fix must NOT add a second server-start — the
    engage keeps EXACTLY ONE ``fabro server start`` for the whole container
    lifetime, and the child attaches to it rather than starting its own
    (lead-oqaw / 728871aca27b0d8f preserved)."""
    script = build_engage_script(tmp_path)
    assert script.count("fabro server start") == 1, (
        "the engage must start EXACTLY ONE per-container fabro server; count="
        f"{script.count('fabro server start')} (lead-oqaw)"
    )


def test_startup_drain_routes_through_shared_run_finite(tmp_path):
    """The startup-drain path and the message-driven path share the SINGLE
    ``run_finite`` worker: there is exactly ONE ``fabro run`` in the whole
    engage, so the one ``--server "$FABRO_SERVER"`` fix covers both paths
    (lead-oqaw)."""
    script = build_engage_script(tmp_path)
    n = count_fabro_run_invocations(script)
    assert n == 1, (
        "both the message-driven and startup-drain paths must route through the "
        f"single `run_finite` `fabro run`; count={n} (lead-oqaw)"
    )
    # drain -> dispatch -> run_finite, and dispatch -> run_finite.
    i_drain = script.index("drain() {")
    i_dispatch = script.index("dispatch() {")
    i_run_finite = script.index("run_finite() {")
    assert i_run_finite < i_dispatch < i_drain, (
        "run_finite must be defined before dispatch and drain that call it; "
        f"run_finite@{i_run_finite}, dispatch@{i_dispatch}, drain@{i_drain} "
        "(lead-oqaw)"
    )
    assert 'dispatch "$_dr_wid"' in script, (
        "the drain must route each pending work_id through dispatch (lead-oqaw)"
    )
    assert 'run_finite "$_d_wid"' in script, (
        "dispatch must fire the shared run_finite worker (lead-oqaw)"
    )


def test_finite_child_server_flag_binds_container_socket(tmp_path):
    """The shared server the finite child attaches to is the container-scoped
    socket the ONE server binds — ``FABRO_SERVER`` targets
    ``FABRO_WATCH_SERVER_SOCKET`` (lead-oqaw)."""
    script = build_engage_script(tmp_path)
    assert f"FABRO_SERVER={FABRO_WATCH_SERVER_SOCKET}" in script, (
        "the finite child must target the shared server at the container socket "
        f"{FABRO_WATCH_SERVER_SOCKET} via FABRO_SERVER (lead-oqaw)"
    )
