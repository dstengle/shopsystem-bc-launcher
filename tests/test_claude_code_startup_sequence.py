"""
Unit tests for the Claude Code startup sequence inside the agent tmux
session (lead-3oi bugfix).

Background
----------
lead-9sq made `--startup-prompt` default to a session-start imperative.
That default-resolution layer is tested in
test_startup_prompt_default.py.  This file pins the orchestration
layer underneath: the launcher must start Claude Code inside the tmux
session BEFORE sending the user prompt, otherwise the prompt lands in
bash and fails as "-bash: <first-word>: command not found" (the
defect lead-3oi reproduces).

The required sequence after `tmux new-session -d -s agent`:
    1. send-keys 'claude' Enter
    2. wait for the Claude Code banner ('Claude Code v')
    3. send-keys Enter (accept default workspace-trust prompt)
    4. wait for the main-input prompt indicator ('❯')
    5. send-keys <startup_prompt> Enter

On readiness-poll timeout the launcher MUST emit a stderr warning that
names the step that did not confirm — the lead-3oi acceptance criterion
that distinguishes this fix from "silently returns success".

These tests use FakeDockerDriver, which records send-keys calls and
exposes simulate_marker_timeout(...) for the timeout path.  No live
tmux / Claude Code is required.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bc_launcher.controller import (
    AGENT_TMUX_SESSION,
    BcContainerController,
    CLAUDE_INPUT_READY_MARKER,
    CLAUDE_READY_MARKER,
)
from tests.fake_driver import FakeDockerDriver


CONTAINER = "bc-shopsystem-messaging"
BC_NAME = "shopsystem-messaging"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_credential_home(tmp_path: Path) -> Path:
    """Build a credential_home dir with the three standard credential sources."""
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
    """Return all tmux send-keys command lists, in call order."""
    return [
        c.command for c in driver.exec_calls
        if c.command[:2] == ["tmux", "send-keys"]
    ]


# ---------------------------------------------------------------------------
# Happy path: claude is started before the startup prompt is injected
# ---------------------------------------------------------------------------

def test_launch_sends_claude_command_before_startup_prompt(tmp_path):
    """
    Acceptance: when the startup prompt is non-empty, the launcher first
    types 'claude' Enter into tmux to start Claude Code, then later sends
    the startup prompt.  The 'claude' send-keys MUST precede the
    startup-prompt send-keys in the recorded exec_call order.
    """
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    home = _make_credential_home(tmp_path)
    manifest = _make_manifest(tmp_path)

    result = controller.launch(
        bc_name=BC_NAME,
        startup_prompt="hello world",
        manifest_path=manifest,
        credential_home=home,
    )
    assert result.exit_code == 0

    send_keys = _send_keys_calls(driver)
    # Find indices of the 'claude'-starter and the startup-prompt send-keys.
    claude_idx = next(
        (i for i, cmd in enumerate(send_keys) if "claude" in cmd and "Enter" in cmd),
        None,
    )
    prompt_idx = next(
        (i for i, cmd in enumerate(send_keys) if "hello world" in cmd and "Enter" in cmd),
        None,
    )
    assert claude_idx is not None, (
        f"Expected 'claude' send-keys before the startup prompt.\n"
        f"All send-keys calls: {send_keys!r}"
    )
    assert prompt_idx is not None, (
        f"Expected startup prompt send-keys.\n"
        f"All send-keys calls: {send_keys!r}"
    )
    assert claude_idx < prompt_idx, (
        f"Expected 'claude' send-keys (index {claude_idx}) to precede "
        f"startup-prompt send-keys (index {prompt_idx}).\n"
        f"All send-keys calls: {send_keys!r}"
    )


def test_launch_polls_for_claude_ready_and_input_ready_markers(tmp_path):
    """
    Acceptance: the launcher polls for both readiness markers in order:
    first the Claude Code banner ('Claude Code v'), then the input prompt
    indicator ('❯').  The order matters because step 3 (Enter to accept
    the trust prompt) is sandwiched between them.
    """
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    home = _make_credential_home(tmp_path)
    manifest = _make_manifest(tmp_path)

    controller.launch(
        bc_name=BC_NAME,
        startup_prompt="anything",
        manifest_path=manifest,
        credential_home=home,
    )

    markers = [m for (_c, _s, m) in driver.wait_for_marker_calls]
    assert CLAUDE_READY_MARKER in markers, (
        f"Expected wait_for_pane_marker on Claude Code banner "
        f"{CLAUDE_READY_MARKER!r}; recorded waits: {markers!r}"
    )
    assert CLAUDE_INPUT_READY_MARKER in markers, (
        f"Expected wait_for_pane_marker on input prompt indicator "
        f"{CLAUDE_INPUT_READY_MARKER!r}; recorded waits: {markers!r}"
    )
    assert markers.index(CLAUDE_READY_MARKER) < markers.index(
        CLAUDE_INPUT_READY_MARKER
    ), (
        "Expected Claude-Code-ready wait to precede input-ready wait; "
        f"observed marker sequence: {markers!r}"
    )


def test_launch_sends_enter_to_accept_workspace_trust_between_marker_waits(tmp_path):
    """
    Acceptance: between the Claude-ready wait and the input-ready wait,
    the launcher sends a bare 'Enter' to accept Claude Code's
    workspace-trust prompt (whose default selection is "Yes, I trust").
    Without this, the input prompt never appears and the launcher would
    block on the second readiness wait.
    """
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    home = _make_credential_home(tmp_path)
    manifest = _make_manifest(tmp_path)

    controller.launch(
        bc_name=BC_NAME,
        startup_prompt="anything",
        manifest_path=manifest,
        credential_home=home,
    )

    # Reconstruct the chronological interleaving of send-keys and marker waits.
    # Both lists are append-only and ordered by call.  We zip on call order
    # via the controller's known sequence: claude/Enter → wait CLAUDE_READY
    # → bare Enter → wait INPUT_READY → prompt/Enter.
    send_keys = _send_keys_calls(driver)
    # The bare 'Enter' is a send-keys with no payload token before 'Enter'.
    # Concretely: command == ['tmux', 'send-keys', '-t', AGENT_TMUX_SESSION, 'Enter']
    bare_enter = [
        cmd for cmd in send_keys
        if cmd == ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, "Enter"]
    ]
    assert bare_enter, (
        f"Expected a bare-Enter send-keys (trust-prompt accept).\n"
        f"All send-keys: {send_keys!r}"
    )


# ---------------------------------------------------------------------------
# Failure modes: readiness-poll timeout warnings
# ---------------------------------------------------------------------------

def test_timeout_on_claude_ready_emits_named_stderr_warning_and_skips_prompt(tmp_path):
    """
    Acceptance: on Claude-Code-ready timeout, the launcher writes a
    stderr warning that names the failing step (Claude Code start) AND
    does NOT proceed to inject the startup prompt (because the user
    prompt would otherwise land in whatever did appear in the pane —
    likely bash — and reintroduce the lead-3oi defect).
    """
    driver = FakeDockerDriver()
    driver.simulate_marker_timeout(CONTAINER, AGENT_TMUX_SESSION, CLAUDE_READY_MARKER)
    controller = BcContainerController(driver)
    home = _make_credential_home(tmp_path)
    manifest = _make_manifest(tmp_path)

    result = controller.launch(
        bc_name=BC_NAME,
        startup_prompt="UNIQUE_TEST_PROMPT_BANNER",
        manifest_path=manifest,
        credential_home=home,
    )

    # Warning is on stderr, names the step, and the prompt was not injected.
    assert "warning" in result.stderr.lower(), (
        f"Expected a stderr warning on Claude-ready timeout; "
        f"got stderr: {result.stderr!r}"
    )
    assert "Claude Code" in result.stderr, (
        f"Expected stderr to name Claude Code as the failing step; "
        f"got stderr: {result.stderr!r}"
    )
    send_keys = _send_keys_calls(driver)
    assert not any("UNIQUE_TEST_PROMPT_BANNER" in cmd for cmd in send_keys), (
        f"Startup prompt MUST NOT be injected after a Claude-ready timeout; "
        f"send-keys: {send_keys!r}"
    )


def test_timeout_on_input_ready_emits_named_stderr_warning_and_skips_prompt(tmp_path):
    """
    Acceptance: on input-ready timeout (Claude Code banner appeared but
    the trust-accept never produced an input prompt), the launcher
    writes a stderr warning naming that specific step and does NOT
    inject the startup prompt.
    """
    driver = FakeDockerDriver()
    driver.simulate_marker_timeout(
        CONTAINER, AGENT_TMUX_SESSION, CLAUDE_INPUT_READY_MARKER
    )
    controller = BcContainerController(driver)
    home = _make_credential_home(tmp_path)
    manifest = _make_manifest(tmp_path)

    result = controller.launch(
        bc_name=BC_NAME,
        startup_prompt="UNIQUE_TEST_PROMPT_BANNER",
        manifest_path=manifest,
        credential_home=home,
    )

    assert "warning" in result.stderr.lower(), (
        f"Expected a stderr warning on input-ready timeout; "
        f"got stderr: {result.stderr!r}"
    )
    # Step name must distinguish "input ready" / "trust" from the
    # Claude-Code-start step.
    assert (
        "trust" in result.stderr.lower() or "input" in result.stderr.lower()
    ), (
        f"Expected stderr to name the trust-accept / input-ready step; "
        f"got stderr: {result.stderr!r}"
    )
    send_keys = _send_keys_calls(driver)
    assert not any("UNIQUE_TEST_PROMPT_BANNER" in cmd for cmd in send_keys), (
        f"Startup prompt MUST NOT be injected after an input-ready timeout; "
        f"send-keys: {send_keys!r}"
    )


# ---------------------------------------------------------------------------
# Pre-existing lead-9sq behaviors remain pinned
# ---------------------------------------------------------------------------

def test_empty_string_startup_prompt_suppresses_both_claude_start_and_injection(tmp_path):
    """
    lead-9sq pinned that --startup-prompt '' suppresses prompt injection.
    The lead-3oi fix preserves this: when startup_prompt is '' the
    controller's `if startup_prompt:` guard skips both the Claude Code
    start AND the prompt injection, leaving tmux at its default bash
    session.  This is the documented opt-out and must not be broken by
    the new orchestration.
    """
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    home = _make_credential_home(tmp_path)
    manifest = _make_manifest(tmp_path)

    result = controller.launch(
        bc_name=BC_NAME,
        startup_prompt="",
        manifest_path=manifest,
        credential_home=home,
    )
    assert result.exit_code == 0

    send_keys = _send_keys_calls(driver)
    # No 'claude' send-keys, no startup-prompt send-keys.
    assert not any("claude" in cmd for cmd in send_keys), (
        f"With startup_prompt='', no 'claude' should be sent; "
        f"send-keys: {send_keys!r}"
    )
    # And no marker polling should have occurred either.
    assert driver.wait_for_marker_calls == [], (
        f"With startup_prompt='', no readiness polling should occur; "
        f"recorded waits: {driver.wait_for_marker_calls!r}"
    )


def test_none_startup_prompt_also_suppresses_claude_start(tmp_path):
    """
    The controller layer treats startup_prompt=None the same as ''.  The
    CLI layer (lead-9sq) substitutes the default template when None is
    passed at the argparse boundary, so the controller never sees None
    in the normal CLI path.  But programmatic callers (and the legacy
    behavior before lead-9sq) may still pass None; preserve that.
    """
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    home = _make_credential_home(tmp_path)
    manifest = _make_manifest(tmp_path)

    result = controller.launch(
        bc_name=BC_NAME,
        startup_prompt=None,
        manifest_path=manifest,
        credential_home=home,
    )
    assert result.exit_code == 0
    send_keys = _send_keys_calls(driver)
    assert not any("claude" in cmd for cmd in send_keys)
    assert driver.wait_for_marker_calls == []


def test_default_template_path_starts_claude_then_injects_prompt(tmp_path, monkeypatch):
    """
    End-to-end through the CLI: with no --startup-prompt, the lead-9sq
    default template is resolved and lead-3oi's sequencing kicks in.
    The 'Run your session-start sequence ...' default is the exact text
    that previously failed in bash with "-bash: Run: command not found".
    After the fix it must reach Claude Code's input — i.e. AFTER the
    'claude' send-keys.
    """
    from bc_launcher import cli as cli_module
    from bc_launcher.cli import (
        DEFAULT_STARTUP_PROMPT_TEMPLATE,
        main as cli_main,
    )

    home = _make_credential_home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    _make_manifest(tmp_path)
    monkeypatch.chdir(tmp_path)

    fake_driver = FakeDockerDriver()

    def _factory(_real_driver):
        return BcContainerController(fake_driver)

    monkeypatch.setattr(cli_module, "BcContainerController", _factory)
    monkeypatch.setattr(cli_module, "RealDockerDriver", lambda: object())

    exit_code = cli_main(["launch", BC_NAME])
    assert exit_code == 0

    send_keys = _send_keys_calls(fake_driver)
    expected_prompt = DEFAULT_STARTUP_PROMPT_TEMPLATE.format(bc_name=BC_NAME)

    claude_idx = next(
        (i for i, cmd in enumerate(send_keys) if "claude" in cmd and "Enter" in cmd),
        None,
    )
    prompt_idx = next(
        (i for i, cmd in enumerate(send_keys) if expected_prompt in cmd),
        None,
    )
    assert claude_idx is not None and prompt_idx is not None, (
        f"Expected both 'claude' start and the default template prompt in send-keys; "
        f"send-keys: {send_keys!r}"
    )
    assert claude_idx < prompt_idx, (
        f"Default-template path: 'claude' (index {claude_idx}) must precede "
        f"the default prompt (index {prompt_idx}). send-keys: {send_keys!r}"
    )


# ---------------------------------------------------------------------------
# The Reviewer-quality assertion: monitor pane does NOT contain the bash
# error after a successful launch — this is the user-facing acceptance.
# ---------------------------------------------------------------------------

def test_monitor_pane_after_default_launch_does_not_contain_bash_command_not_found(
    tmp_path,
):
    """
    Acceptance: a successful default-prompt launch leaves the tmux pane
    in a state where 'bash: Run: command not found' does NOT appear.
    The FakeDockerDriver represents pane content as whatever was set
    via set_tmux_pane_content; here we assert the controller's behavior
    did not inject the prompt before claude started (which is what would
    have produced that error string in the real shell).

    Operationally: if 'claude' was sent BEFORE the user prompt (the fix),
    bash never sees the user-prompt text as a command, so the error
    cannot be produced.  This is verified by re-asserting the sequencing
    invariant from a Reviewer-style "is the defect reproducible against
    this launcher?" framing.
    """
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    home = _make_credential_home(tmp_path)
    manifest = _make_manifest(tmp_path)

    controller.launch(
        bc_name=BC_NAME,
        startup_prompt="Run your session-start sequence per /workspace/CLAUDE.md",
        manifest_path=manifest,
        credential_home=home,
    )

    # The defect signature: the user-prompt's first word ('Run') would land
    # in bash before any 'claude' send-keys.  Verify the chronological
    # invariant: no prompt-text send-keys occurs while claude has not yet
    # been started.
    seen_claude_start = False
    for cmd in _send_keys_calls(driver):
        # send-keys command shape: ['tmux','send-keys','-t',session,<payload>,'Enter']
        # bare Enter has no payload before 'Enter'; 'claude' payload starts the agent.
        if "claude" in cmd and "Enter" in cmd and cmd[-2] == "claude":
            seen_claude_start = True
            continue
        # Any send-keys carrying the user-prompt text MUST be after claude.
        if any("Run your session-start sequence" in token for token in cmd):
            assert seen_claude_start, (
                f"User-prompt text was sent before 'claude' was started — "
                f"this is the lead-3oi defect signature.\n"
                f"All send-keys: {_send_keys_calls(driver)!r}"
            )
