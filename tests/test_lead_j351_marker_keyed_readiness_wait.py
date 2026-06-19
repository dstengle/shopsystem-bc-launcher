"""
Unit tests for the marker-keyed (progress-based) readiness wait
(lead-j351 bugfix, scenario @scenario_hash:d227ccbcc9bdfa87).

Background
----------
The Claude Code startup sequence (pinned by lead-3oi / lead-5ig in
test_claude_code_startup_sequence.py) waits on two pane markers before
injecting the startup prompt:

    CLAUDE_READY_MARKER        — PRE-trust workspace-trust banner
    CLAUDE_INPUT_READY_MARKER  — POST-trust input-ready footer

The legacy implementation passed a FIXED wall-clock budget,
CLAUDE_READINESS_TIMEOUT_SECONDS = 60.0, straight into
``wait_for_pane_marker`` as an absolute deadline:

    deadline = time.monotonic() + 60.0
    ... return False once `now >= deadline`, even if the marker would
        appear at second 61.

BUG (confirmed live, v0.3.3): a *brokered* boot DOES reach the agent
REPL, but it takes longer than 60 s.  The fixed 60 s deadline fires
BEFORE the input-ready marker appears, so launch reports "startup prompt
NOT injected" and the operator must manually `bc-container inject`.

FIX: the readiness wait must NOT abandon injection at a fixed 60 s
wall-clock deadline while the agent is still PROGRESSING toward its
observable input-ready marker.  It must key on the observable marker —
keep polling as long as the pane is still changing (the boot is still
making progress), treating the timeout as a *no-progress / idle* budget
rather than an absolute wall-clock cap.  A slow brokered boot whose
marker appears only after >60 s still gets its prompt injected.

These tests genuinely DISTINGUISH "marker-keyed / progress-based" from
"fixed 60 s deadline":

  * The simulated pane KEEPS CHANGING (makes progress) the whole time and
    only emits the marker at simulated t ~= 75 s, well past the legacy
    60 s deadline.
  * A fixed-60 s-deadline implementation returns False here (marker never
    seen within 60 s); only a marker-keyed / progress-based wait returns
    True.
  * No real wall-clock time elapses — the clock and the pane capture are
    injected, so the >60 s boot is simulated deterministically.
"""
from __future__ import annotations

from pathlib import Path

from bc_launcher.controller import (
    AGENT_TMUX_SESSION,
    BcContainerController,
    CLAUDE_INPUT_READY_MARKER,
    CLAUDE_READINESS_TIMEOUT_SECONDS,
)
from bc_launcher.driver import RealDockerDriver
from tests.fake_driver import FakeDockerDriver


CONTAINER = "bc-shopsystem-messaging"
BC_NAME = "shopsystem-messaging"


# ---------------------------------------------------------------------------
# Helpers (mirror test_claude_code_startup_sequence.py)
# ---------------------------------------------------------------------------

def _make_credential_home(tmp_path: Path) -> Path:
    home = tmp_path / "fake_home"
    home.mkdir()
    (home / ".claude").mkdir()
    (home / ".config" / "gh").mkdir(parents=True)
    (home / ".gitconfig").write_text("")
    return home


def _make_manifest(tmp_path: Path, bc_name: str = BC_NAME) -> Path:
    manifest = tmp_path / "bc-manifest.yaml"
    manifest.write_text(
        f"product: shopsystem product\n"
        f"bcs:\n"
        f"  - name: {bc_name}\n"
        f"    remote: https://github.com/shopsystem/{bc_name}.git\n"
        f"    role: bc\n"
    )
    return manifest


def _send_keys_calls(driver: FakeDockerDriver) -> list[list[str]]:
    return [
        c.command for c in driver.exec_calls
        if c.command[:2] == ["tmux", "send-keys"]
    ]


# ---------------------------------------------------------------------------
# A simulated tmux pane whose contents keep changing (the boot keeps
# making progress) and which only emits the readiness marker AFTER a
# configured number of simulated seconds — modelling a slow brokered boot.
# ---------------------------------------------------------------------------

class _SimulatedClock:
    """Monotonic clock that advances by `tick` seconds on every read.

    The first read returns the start value; every subsequent read advances
    by `tick`.  Sleep is a no-op that also advances the clock, so the
    poll loop's `time.sleep(poll_interval)` consumes simulated, not real,
    time.
    """

    def __init__(self, start: float = 0.0, tick: float = 0.5) -> None:
        self._now = start
        self._tick = tick

    def monotonic(self) -> float:
        now = self._now
        self._now += self._tick
        return now

    def sleep(self, seconds: float) -> None:
        # Advance simulated time by the requested sleep, no real blocking.
        self._now += seconds


class _SlowBrokeredPane:
    """Yields pane contents that keep CHANGING (progress) every poll, and

    only contains `marker` once `marker_appears_after_seconds` of simulated
    time has elapsed.  `clock` is the same _SimulatedClock the wait loop
    reads, so "elapsed" is measured on the simulated timeline.
    """

    def __init__(
        self,
        clock: _SimulatedClock,
        marker: str,
        marker_appears_after_seconds: float,
    ) -> None:
        self._clock = clock
        self._marker = marker
        self._after = marker_appears_after_seconds
        self._start = clock._now
        self._poll = 0

    def capture(self) -> str:
        # Progress: every poll the pane shows a different boot line, so a
        # progress-based wait sees the boot is still advancing.
        self._poll += 1
        elapsed = self._clock._now - self._start
        body = f"booting brokered agent... step {self._poll} (t={elapsed:.1f}s)\n"
        if elapsed >= self._after:
            body += self._marker + "\n"
        return body


# ---------------------------------------------------------------------------
# Layer A — the bug's true location: RealDockerDriver.wait_for_pane_marker
# ---------------------------------------------------------------------------

def test_wait_keys_on_marker_not_fixed_deadline_for_slow_brokered_boot():
    """A brokered boot whose marker appears only after >60 s of *progressing*

    output is still observed: the wait keys on the marker / progress, not a
    fixed 60 s wall-clock deadline.

    DISTINGUISHER: the marker appears at simulated t = 75 s, past the legacy
    60 s CLAUDE_READINESS_TIMEOUT_SECONDS deadline.  A fixed-deadline
    implementation returns False here; a marker-keyed / progress-based one
    returns True.
    """
    clock = _SimulatedClock(start=0.0, tick=0.0)
    # marker appears at 75 s — comfortably past the legacy 60 s deadline.
    pane = _SlowBrokeredPane(
        clock, CLAUDE_INPUT_READY_MARKER, marker_appears_after_seconds=75.0
    )
    driver = RealDockerDriver()

    observed = driver.wait_for_pane_marker(
        CONTAINER,
        AGENT_TMUX_SESSION,
        CLAUDE_INPUT_READY_MARKER,
        CLAUDE_READINESS_TIMEOUT_SECONDS,
        poll_interval_seconds=1.0,
        _clock=clock,
        _capture=pane.capture,
    )

    assert observed is True, (
        "A slow brokered boot whose input-ready marker appears at t=75s "
        "(past the legacy 60s deadline) but whose pane keeps PROGRESSING "
        "must still be observed: the wait keys on the marker, not a fixed "
        "60s wall-clock deadline."
    )


def test_wait_abandons_only_after_no_progress_idle_budget_not_fixed_60s():
    """The timeout is a NO-PROGRESS (idle) budget, not a fixed wall-clock cap.

    When the pane STOPS changing (no progress) and the marker never appears,
    the wait abandons after the idle budget — proving the termination
    condition is keyed on progress/marker, not on a fixed 60 s wall clock.
    A pane that goes idle immediately is abandoned; a pane that keeps
    progressing is NOT abandoned at 60 s (covered by the test above).
    """
    clock = _SimulatedClock(start=0.0, tick=0.0)

    class _StalledPane:
        """Identical contents every poll (NO progress), marker never present."""

        def capture(self) -> str:
            return "frozen pane: no further output\n"

    pane = _StalledPane()
    driver = RealDockerDriver()

    observed = driver.wait_for_pane_marker(
        CONTAINER,
        AGENT_TMUX_SESSION,
        CLAUDE_INPUT_READY_MARKER,
        CLAUDE_READINESS_TIMEOUT_SECONDS,
        poll_interval_seconds=1.0,
        _clock=clock,
        _capture=pane.capture,
    )

    assert observed is False, (
        "A pane that stops progressing and never shows the marker must be "
        "abandoned once the no-progress idle budget is exhausted."
    )


# ---------------------------------------------------------------------------
# Layer B — the controller scenario (BDD outer loop d227ccbcc9bdfa87)
# ---------------------------------------------------------------------------

def test_slow_brokered_boot_past_60s_still_injects_startup_prompt(tmp_path):
    """Scenario d227ccbcc9bdfa87: a brokered boot that becomes ready ONLY

    after the legacy 60 s deadline still has its startup prompt injected.

    The FakeDockerDriver is configured so the input-ready marker appears
    only after simulated elapsed time exceeds 60 s.  A fixed-60 s-deadline
    controller would emit "startup prompt NOT injected"; a marker-keyed one
    injects the prompt into the "agent" tmux session.
    """
    driver = FakeDockerDriver()
    # The input-ready marker only becomes observable after the boot has been
    # progressing for more than 60 s (slow brokered boot).
    driver.simulate_marker_delayed_past_seconds(
        CONTAINER,
        AGENT_TMUX_SESSION,
        CLAUDE_INPUT_READY_MARKER,
        appears_after_seconds=75.0,
    )
    controller = BcContainerController(driver)
    home = _make_credential_home(tmp_path)
    manifest = _make_manifest(tmp_path)

    result = controller.launch(
        bc_name=BC_NAME,
        startup_prompt="UNIQUE_SLOW_BOOT_PROMPT",
        manifest_path=manifest,
        credential_home=home,
    )

    assert result.exit_code == 0
    # The prompt MUST be injected despite the >60 s boot — into the tmux
    # session named "agent".
    send_keys = _send_keys_calls(driver)
    injected = [
        cmd for cmd in send_keys
        if "UNIQUE_SLOW_BOOT_PROMPT" in cmd
        and cmd[:4] == ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION]
    ]
    assert injected, (
        "A slow brokered boot whose input-ready marker appears only after "
        ">60s must STILL have its startup prompt injected into the 'agent' "
        f"tmux session; send-keys recorded: {send_keys!r}\n"
        f"stderr: {result.stderr!r}"
    )
    assert "NOT injected" not in result.stderr, (
        "Launch must not report 'startup prompt NOT injected' for a slow "
        f"brokered boot still progressing toward readiness; stderr: "
        f"{result.stderr!r}"
    )


def test_inject_after_ready_ordering_preserved_for_slow_boot(tmp_path):
    """The inject-after-ready ORDERING (5ef728039884a9a2) still holds for a

    slow boot: the input-ready marker is waited on BEFORE the startup prompt
    is sent.  The marker-keyed relaxation must not let the prompt jump ahead
    of the readiness barrier.
    """
    driver = FakeDockerDriver()
    driver.simulate_marker_delayed_past_seconds(
        CONTAINER,
        AGENT_TMUX_SESSION,
        CLAUDE_INPUT_READY_MARKER,
        appears_after_seconds=75.0,
    )
    controller = BcContainerController(driver)
    home = _make_credential_home(tmp_path)
    manifest = _make_manifest(tmp_path)

    controller.launch(
        bc_name=BC_NAME,
        startup_prompt="ORDER_CHECK_PROMPT",
        manifest_path=manifest,
        credential_home=home,
    )

    # The input-ready wait must have been recorded, and the startup-prompt
    # send-keys must come AFTER that wait in call order.
    markers = [m for (_c, _s, m) in driver.wait_for_marker_calls]
    assert CLAUDE_INPUT_READY_MARKER in markers, (
        f"Expected an input-ready wait even for a slow boot; "
        f"recorded waits: {markers!r}"
    )
    # The wait for the input-ready marker must precede the prompt injection.
    assert driver.input_ready_wait_preceded_prompt("ORDER_CHECK_PROMPT"), (
        "The startup prompt must be injected only AFTER the input-ready "
        "marker wait (inject-after-ready ordering, 5ef728039884a9a2)."
    )
