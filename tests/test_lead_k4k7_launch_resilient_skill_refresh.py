"""
Unit tests for launch resilience to a transient shop-templates skill-refresh
failure, plus the `bc-container start-agent <bc>` recovery subcommand
(lead-k4k7 bugfix).

Background
----------
The bc-container launch flow runs the shop-templates skill-refresh step
(`shop-templates update --target /workspace --shop-type <bc|lead>`) AFTER the
repo clone and beads provisioning but BEFORE the agent-start step (tmux
new-session + `agent-vault run -- claude` + readiness barriers + inject).

BUG (observed live 2026-06-19): a transient (network-blip) non-zero exit from
`shop-templates update` made launch `return CommandResult(exit_code=1)` WITHOUT
starting tmux/claude.  Because the container + clone already completed, the
result was an "Up (healthy)" container with the repo cloned but NO agent
session — the "Started tmux session" line at controller.py:1070 was never
emitted.  The early return was non-resumable: every relaunch re-cloned and
re-stranded, and the only recovery was driving the agent-start sequence by
hand against the already-healthy container.

REMEDY (this change):
  (b) DOWNGRADE the skill-refresh failure from fatal-early-return to a WARNING
      that still PROCEEDS to the agent-start step.  The skill-refresh is a
      freshness nicety, not a precondition for the agent to run; a stale-but-
      present skill set beats a healthy container with no agent.
  (c) ADD a `bc-container start-agent <bc>` recovery subcommand that drives the
      SAME agent-start sequence (shared with launch) against an already-cloned,
      healthy container WITHOUT re-cloning, idempotent / safe to re-run.

These tests cover BOTH acceptance criteria and pin that the existing launch
contracts (clone, the two readiness barriers, inject ordering) continue to
hold.
"""
from __future__ import annotations

from pathlib import Path

from bc_launcher.controller import (
    AGENT_TMUX_SESSION,
    BcContainerController,
)
from tests.fake_driver import FakeDockerDriver


CONTAINER = "bc-shopsystem-messaging"
BC_NAME = "shopsystem-messaging"
REPO_URL = "https://github.com/shopsystem/shopsystem-messaging.git"


# ---------------------------------------------------------------------------
# Helpers (mirror test_lead_j351_marker_keyed_readiness_wait.py)
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


def _tmux_new_session_calls(driver: FakeDockerDriver) -> list[list[str]]:
    return [
        c.command for c in driver.exec_calls
        if c.command[:3] == ["tmux", "new-session", "-d"]
    ]


def _send_keys_calls(driver: FakeDockerDriver) -> list[list[str]]:
    return [
        c.command for c in driver.exec_calls
        if c.command[:2] == ["tmux", "send-keys"]
    ]


def _git_clone_calls(driver: FakeDockerDriver) -> list[list[str]]:
    return [
        c.command for c in driver.exec_calls
        if c.command[:2] == ["git", "clone"]
    ]


# ---------------------------------------------------------------------------
# Acceptance criterion (1) — a forced non-zero skill-refresh during launch no
# longer aborts the launch: a warning is logged AND the agent-start sequence
# (tmux new-session + claude start + inject) still runs.
# ---------------------------------------------------------------------------

def test_skill_refresh_failure_warns_and_proceeds_to_agent_start(tmp_path):
    """A non-zero `shop-templates update` during launch must NOT abort:

    launch logs a warning and PROCEEDS to start tmux/claude.  Previously the
    launch did `return CommandResult(exit_code=1)` with NO tmux session
    started; this asserts the "Started tmux session" path runs anyway and the
    startup prompt is injected into the 'agent' tmux session.
    """
    driver = FakeDockerDriver()
    # Force the (valid) `shop-templates update` to exit non-zero — the
    # transient network-blip failure the incident reproduced.
    driver.set_skill_refresh_fails(CONTAINER, True)
    controller = BcContainerController(driver)
    home = _make_credential_home(tmp_path)
    manifest = _make_manifest(tmp_path)

    result = controller.launch(
        bc_name=BC_NAME,
        repo_url=REPO_URL,
        startup_prompt="K4K7_LAUNCH_PROMPT",
        manifest_path=manifest,
        credential_home=home,
    )

    # The agent-start sequence MUST have run despite the refresh failure.
    new_sessions = _tmux_new_session_calls(driver)
    assert any(AGENT_TMUX_SESSION in cmd for cmd in new_sessions), (
        "A skill-refresh failure must NOT abort the launch before the agent "
        f"tmux session is started; tmux new-session calls: {new_sessions!r}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "Started tmux session" in result.stdout, (
        "The 'Started tmux session' log line must still be emitted when the "
        f"skill-refresh fails; stdout: {result.stdout!r}"
    )
    # The startup prompt MUST be injected into the 'agent' tmux session.
    injected = [
        cmd for cmd in _send_keys_calls(driver)
        if "K4K7_LAUNCH_PROMPT" in cmd
        and cmd[:4] == ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION]
    ]
    assert injected, (
        "The startup prompt must be injected after a skill-refresh failure — "
        f"the launch must continue to agent-start; send-keys: "
        f"{_send_keys_calls(driver)!r}"
    )


def test_skill_refresh_failure_logs_warning(tmp_path):
    """A non-zero `shop-templates update` must log a WARNING naming the failed

    skill-refresh — surfaced as a warning, NOT a fatal early-return error.
    """
    driver = FakeDockerDriver()
    driver.set_skill_refresh_fails(CONTAINER, True)
    controller = BcContainerController(driver)
    home = _make_credential_home(tmp_path)
    manifest = _make_manifest(tmp_path)

    result = controller.launch(
        bc_name=BC_NAME,
        repo_url=REPO_URL,
        startup_prompt="K4K7_WARN_PROMPT",
        manifest_path=manifest,
        credential_home=home,
    )

    combined = result.stdout + result.stderr
    assert "warning" in combined.lower(), (
        "A skill-refresh failure must log a warning; "
        f"stdout: {result.stdout!r} stderr: {result.stderr!r}"
    )
    assert "shop-templates" in combined, (
        "The warning must name the failed shop-templates skill-refresh; "
        f"stdout: {result.stdout!r} stderr: {result.stderr!r}"
    )


def test_skill_refresh_failure_launch_exit_sensible(tmp_path):
    """After a skill-refresh failure the launch reaches the agent-start step

    and completes its readiness/inject sequence — exit semantics are sensible
    (exit 0: the agent came up).  The downgrade must not leave the launch
    early-returning exit 1 before tmux start.
    """
    driver = FakeDockerDriver()
    driver.set_skill_refresh_fails(CONTAINER, True)
    controller = BcContainerController(driver)
    home = _make_credential_home(tmp_path)
    manifest = _make_manifest(tmp_path)

    result = controller.launch(
        bc_name=BC_NAME,
        repo_url=REPO_URL,
        startup_prompt="K4K7_EXIT_PROMPT",
        manifest_path=manifest,
        credential_home=home,
    )

    assert result.exit_code == 0, (
        "A launch whose only failure was a transient skill-refresh — with all "
        "readiness barriers green and the prompt injected — must exit 0, not "
        f"abort; stderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Acceptance criterion (2) — `bc-container start-agent <bc>` drives the
# agent-start sequence against an already-cloned healthy container WITHOUT
# re-cloning, and is idempotent on re-run.
# ---------------------------------------------------------------------------

def test_start_agent_brings_up_agent_without_recloning(tmp_path):
    """`start-agent` on an already-cloned, healthy container with no agent runs

    the tmux + agent-vault + inject sequence WITHOUT a clone step.
    """
    driver = FakeDockerDriver()
    # The container is already cloned, running, and healthy — but has no agent.
    driver.set_running(CONTAINER, True)
    controller = BcContainerController(driver)

    result = controller.start_agent(
        bc_name=BC_NAME,
        startup_prompt="K4K7_RECOVERY_PROMPT",
    )

    assert result.exit_code == 0, (
        f"start-agent must succeed on a healthy cloned container; "
        f"stderr: {result.stderr!r}"
    )
    # The agent tmux session must be started.
    new_sessions = _tmux_new_session_calls(driver)
    assert any(AGENT_TMUX_SESSION in cmd for cmd in new_sessions), (
        f"start-agent must start the 'agent' tmux session; "
        f"tmux new-session calls: {new_sessions!r}"
    )
    # NO clone step may run in start-agent.
    assert _git_clone_calls(driver) == [], (
        "start-agent must NOT re-clone the repository; clone calls: "
        f"{_git_clone_calls(driver)!r}"
    )
    # The startup prompt must be injected into the 'agent' tmux session.
    injected = [
        cmd for cmd in _send_keys_calls(driver)
        if "K4K7_RECOVERY_PROMPT" in cmd
        and cmd[:4] == ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION]
    ]
    assert injected, (
        "start-agent must inject the startup prompt into the 'agent' tmux "
        f"session; send-keys: {_send_keys_calls(driver)!r}"
    )


def test_start_agent_runs_agent_vault_wrapped_claude(tmp_path):
    """start-agent must start claude wrapped as `agent-vault run -- claude

    --dangerously-skip-permissions` (the same wrapper launch uses), so the
    broker substitutes credentials and the agent runs as a non-root user.
    """
    driver = FakeDockerDriver()
    driver.set_running(CONTAINER, True)
    controller = BcContainerController(driver)

    controller.start_agent(
        bc_name=BC_NAME,
        startup_prompt="K4K7_VAULT_PROMPT",
    )

    vault_starts = [
        c for c in driver.exec_calls
        if c.command[:2] == ["tmux", "send-keys"]
        and any("agent-vault run -- claude" in tok for tok in c.command)
    ]
    assert vault_starts, (
        "start-agent must start claude wrapped as 'agent-vault run -- "
        f"claude ...'; send-keys: {_send_keys_calls(driver)!r}"
    )
    # The agent-start clients must run as the unprivileged vscode user.
    assert all(c.user == "vscode" for c in vault_starts), (
        "The claude start must run as the vscode user (claude refuses "
        "--dangerously-skip-permissions as root)."
    )


def test_start_agent_idempotent_on_rerun(tmp_path):
    """start-agent must be safe to re-run: a second invocation against the same

    already-cloned healthy container succeeds and still does NOT re-clone.
    """
    driver = FakeDockerDriver()
    driver.set_running(CONTAINER, True)
    controller = BcContainerController(driver)

    first = controller.start_agent(
        bc_name=BC_NAME, startup_prompt="K4K7_IDEM_PROMPT"
    )
    second = controller.start_agent(
        bc_name=BC_NAME, startup_prompt="K4K7_IDEM_PROMPT"
    )

    assert first.exit_code == 0
    assert second.exit_code == 0, (
        f"start-agent must be safe to re-run; stderr: {second.stderr!r}"
    )
    assert _git_clone_calls(driver) == [], (
        "Re-running start-agent must never re-clone; clone calls: "
        f"{_git_clone_calls(driver)!r}"
    )


def test_start_agent_honors_messaging_readiness_barrier(tmp_path):
    """start-agent must share launch's readiness barriers: with an unreachable

    messaging DB it does NOT inject the startup prompt (the barrier still
    gates the prompt, identical to launch).
    """
    driver = FakeDockerDriver()
    driver.set_running(CONTAINER, True)
    dsn = "postgresql://unreachable-db/shopmsg"
    driver.set_dsn_reachable(dsn, False)
    controller = BcContainerController(driver)

    result = controller.start_agent(
        bc_name=BC_NAME,
        startup_prompt="K4K7_BARRIER_PROMPT",
        shopmsg_dsn=dsn,
    )

    injected = [
        cmd for cmd in _send_keys_calls(driver)
        if "K4K7_BARRIER_PROMPT" in cmd
    ]
    assert injected == [], (
        "start-agent must honour the messaging-readiness barrier and NOT "
        f"inject the prompt when the DB is unreachable; send-keys: "
        f"{_send_keys_calls(driver)!r}"
    )
    assert result.exit_code != 0, (
        "start-agent must report a non-zero exit when the messaging-readiness "
        f"barrier fails; stderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# CLI wiring — `bc-container start-agent <bc>` is a first-class subcommand
# that threads its flags to controller.start_agent.
# ---------------------------------------------------------------------------

def test_cli_start_agent_subcommand_parses():
    """The `start-agent` subcommand parses with its bc_name and flags."""
    from bc_launcher.cli import build_parser

    args = build_parser().parse_args(
        ["start-agent", BC_NAME,
         "--shopmsg-dsn", "postgresql://db/shopmsg",
         "--agent-vault-broker", "https://agent-vault:14321",
         "--startup-prompt", "RECOVER"]
    )
    assert args.subcommand == "start-agent"
    assert args.bc_name == BC_NAME
    assert args.shopmsg_dsn == "postgresql://db/shopmsg"
    assert args.agent_vault_broker == "https://agent-vault:14321"
    assert args.startup_prompt == "RECOVER"


def test_cli_start_agent_dispatches_to_controller(monkeypatch):
    """`bc-container start-agent <bc>` dispatches to controller.start_agent

    and threads the bc_name + flags through.
    """
    import bc_launcher.cli as cli_module
    from bc_launcher.cli import main as cli_main
    from bc_launcher.controller import CommandResult

    calls: list[dict] = []

    class _Recorder:
        def start_agent(self, **kwargs):
            calls.append(kwargs)
            return CommandResult(exit_code=0, stdout="", stderr="")

    monkeypatch.setattr(cli_module, "BcContainerController", lambda _d: _Recorder())
    monkeypatch.setattr(cli_module, "RealDockerDriver", lambda: object())

    exit_code = cli_main(
        ["start-agent", BC_NAME,
         "--shopmsg-dsn", "postgresql://db/shopmsg",
         "--agent-vault-broker", "https://agent-vault:14321",
         "--startup-prompt", "RECOVER"]
    )
    assert exit_code == 0
    assert calls, "start-agent subcommand did not dispatch to controller.start_agent"
    call = calls[0]
    assert call["bc_name"] == BC_NAME
    assert call["shopmsg_dsn"] == "postgresql://db/shopmsg"
    assert call["agent_vault_broker"] == "https://agent-vault:14321"
    assert call["startup_prompt"] == "RECOVER"
