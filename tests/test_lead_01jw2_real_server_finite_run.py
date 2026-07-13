"""REAL-fabro-server acceptance for the watcher engage binding the ONE shared
per-container server to EXACTLY the exported ``FABRO_SERVER`` address, so each
finite ``fabro run --server "$FABRO_SERVER"`` child CONNECTS to it (scenarios
ab9b2be40558cfc2 + 33488b7e1657b7c7 + 89e975a7a38fdcaf, work_id lead-01jw.2).

This RETIRES the address-blind stub pins 9f785e78ed55da4b + 32009f85a099be62
(their FakeDockerDriver model of a fabro server binds no socket at the agreed
address, so it could never expose the v0.3.68 bind!=export defect).

The pytest-bdd bindings live below; the load-bearing direct integration test
brings the ONE shared server up via the REAL engage bootstrap and fires a REAL
finite child against it, asserting connection-accepted, resident-count-1, and
real active->completed telemetry — RED against the unfixed engage, GREEN once
the engage binds the shared server to the exported FABRO_SERVER address.
"""
from __future__ import annotations

from pytest_bdd import scenarios

from tests.steps.real_server_finite_run import (  # noqa: F401  (step registration)
    build_and_start_shared_server,
    drive_finite_run_to_real_terminal,
    fire_finite_child,
    server_run_events,
    teardown_server,
    _fabro_listener_lines,
)

scenarios("../features/bc_container_fabro_finite_run_shared_server.feature")


def test_real_fabro_server_finite_run_connects_at_agreed_bind_equals_fabro_server_address(
    tmp_path, monkeypatch
):
    """LOAD-BEARING, non-stub-satisfiable: a REAL fabro server, brought up by the
    REAL engage bootstrap and bound to the SAME address the engage exports as
    FABRO_SERVER, accepts a REAL ``fabro run --server "$FABRO_SERVER"`` finite
    child — no "Server already running", resident count EXACTLY 1, and the real
    server's own telemetry records the run active->completed.

    RED against the UNFIXED engage: the shared SOCKET server never starts
    (SESSION_SECRET missing under the custom --storage-dir), so the child cannot
    connect at the agreed address and reproduces the v0.3.68 failure. GREEN once
    the engage stops the install daemon and exports the install-written
    SESSION_SECRET so the ONE server binds the exported FABRO_SERVER socket.
    """
    server = build_and_start_shared_server(tmp_path, monkeypatch)
    try:
        # (1) bind == exported FABRO_SERVER, read off the REAL recorded script.
        assert server["bind_addr"] and server["bind_addr"] == server["export_addr"], (
            "engage must BIND the shared server to EXACTLY the exported "
            f"FABRO_SERVER; bind={server['bind_addr']!r} "
            f"export={server['export_addr']!r}"
        )

        # (2) the ONE shared server is really UP at the agreed address.
        assert server["socket_up"], (
            "the ONE shared server must be listening at the agreed socket; the "
            "unfixed engage leaves it dead (SESSION_SECRET). bringup log tail:\n"
            f"{server['bringup_log'].read_text()[-1000:]}"
        )
        assert len(_fabro_listener_lines()) == 1, (
            "resident fabro-server count must be EXACTLY 1 after bring-up; got "
            f"{_fabro_listener_lines()!r}"
        )

        # (3) a REAL finite child connects + drives to a real terminal.
        res = drive_finite_run_to_real_terminal(server, "lead-01jw2-real")
        child = res["child"]
        assert not child["server_already_running"], (
            "the real finite child must CONNECT to the shared server, not hit "
            f"'Server already running'; stdout+stderr="
            f"{(child['stdout'] + child['stderr'])!r}"
        )
        assert child["run_id"], (
            "the real finite run must be ACCEPTED (a run id issued) by the "
            f"running shared server; stdout={child['stdout']!r} "
            f"stderr={child['stderr']!r}"
        )
        assert child["resident_servers"] == 1, (
            "resident fabro-server count must stay EXACTLY 1 (child attached, did "
            f"not bind a second server); got {child['resident_servers']}"
        )
        assert res["went_active"] and res["went_completed"], (
            "the REAL shared server's own telemetry must record run.running "
            f"(ACTIVE) -> run.completed (succeeded); events head={res['events'][:800]!r}"
        )
        assert res["telemetry_names_work_id"], (
            "the real-server telemetry must name the finite run's work_id"
        )

        # (4) negative control — a DISAGREEING address does NOT land a completed
        # run on our shared server (the proof is genuinely address-sensitive).
        from pathlib import Path

        bogus = Path(server["def_dir"]) / ".watch" / "disagreeing.sock"
        neg = fire_finite_child(server, "lead-01jw2-neg", server_override=bogus)
        neg_landed = False
        if neg["run_id"]:
            neg_events = server_run_events(server, neg["run_id"])
            neg_landed = (
                '"event":"run.completed"' in neg_events
                and "lead-01jw2-neg" in neg_events
            )
        assert not neg_landed, (
            "a finite run pointed at a DISAGREEING address must NOT land a "
            "completed run on the shared server at the agreed address"
        )
    finally:
        teardown_server(server)
