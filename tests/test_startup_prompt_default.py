"""
Unit tests for the --startup-prompt default-resolution logic in
bc_launcher.cli.

Background: lead-9sq made the --startup-prompt flag default to a
session-start imperative (with {bc_name} substituted) so that BCs
launched without an explicit prompt autonomously arm Monitor and
drain pending inbox.  An explicit value remains a total override.

These tests exercise the CLI layer directly (build_parser + the
resolution branch in main()) without going through BDD scenarios,
per the maintenance brief's "no new scenarios" directive.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bc_launcher import cli as cli_module
from bc_launcher.cli import (
    DEFAULT_STARTUP_PROMPT_TEMPLATE,
    build_parser,
    main as cli_main,
)
from bc_launcher.controller import BcContainerController
from tests.fake_driver import FakeDockerDriver


# ---------------------------------------------------------------------------
# Template content invariants (load-bearing per lead-9sq)
# ---------------------------------------------------------------------------

def test_default_template_directs_arming_monitor():
    """Load-bearing property (a): the template tells the agent to arm Monitor."""
    assert "shop-msg watch --bc {bc_name}" in DEFAULT_STARTUP_PROMPT_TEMPLATE


def test_default_template_directs_draining_inbox():
    """Load-bearing property (b): the template tells the agent to drain inbox."""
    assert "shop-msg pending inbox --bc {bc_name}" in DEFAULT_STARTUP_PROMPT_TEMPLATE


def test_default_template_directs_autonomous_process_to_gated_work_done():
    """Load-bearing property (c), RESTORED by lead-ew86 (ADR-050 D3 /
    ADR-018 D1-D2): the template directs the agent to AUTONOMOUSLY PROCESS each
    pending dispatch through the Implementer->Reviewer loop to a Reviewer-gated
    work_done, WITHOUT waiting for a human 'go'.  The ADR-050 split had
    regressed this to 'await user direction' (drain-then-park); this pins the
    autonomous drain-AND-process default and the ABSENCE of the park directive.
    """
    assert "process each pending dispatch" in DEFAULT_STARTUP_PROMPT_TEMPLATE
    assert "Implementer->Reviewer" in DEFAULT_STARTUP_PROMPT_TEMPLATE
    assert "work_done" in DEFAULT_STARTUP_PROMPT_TEMPLATE
    assert "await user direction" not in DEFAULT_STARTUP_PROMPT_TEMPLATE


def test_default_template_substitutes_bc_name():
    """The template is a format-string with {bc_name} that gets substituted."""
    rendered = DEFAULT_STARTUP_PROMPT_TEMPLATE.format(bc_name="shopsystem-foo")
    assert "shopsystem-foo" in rendered
    assert "{bc_name}" not in rendered


# ---------------------------------------------------------------------------
# argparse default
# ---------------------------------------------------------------------------

def test_argparse_default_for_startup_prompt_is_none():
    """
    The argparse default for --startup-prompt is None.  The CLI's main()
    detects None and substitutes the template; this makes the explicit
    'foo' case distinguishable from the omission case.
    """
    parser = build_parser()
    args = parser.parse_args(["launch", "shopsystem-messaging"])
    assert args.startup_prompt is None


def test_argparse_passes_explicit_value_through_unchanged():
    """An explicit --startup-prompt 'foo' lands in args.startup_prompt as 'foo'."""
    parser = build_parser()
    args = parser.parse_args(
        ["launch", "shopsystem-messaging", "--startup-prompt", "foo"]
    )
    assert args.startup_prompt == "foo"


# ---------------------------------------------------------------------------
# --help shows the default
# ---------------------------------------------------------------------------

def test_launch_help_shows_default_template():
    """
    Acceptance criterion 3: bc-container launch --help must show the
    default in the flag's help string so operators see what will be
    injected when they omit the flag.
    """
    parser = build_parser()
    # argparse format_help() includes the help= text we provided.
    # Drill into the 'launch' subparser to get its formatted help.
    # Easier: just verify the substring is in the parser's full help.
    # The launch help is rendered via the launch subparser.
    sub_actions = [
        a for a in parser._actions
        if isinstance(a, type(parser._subparsers._actions[1])  # SubParsersAction marker
                      if parser._subparsers else type(None))
    ]
    # Simpler approach: invoke `launch --help` via subprocess against the
    # installed entrypoint, mirroring how operators see it.
    bc_container_path = Path(sys.executable).parent / "bc-container"
    result = subprocess.run(
        [str(bc_container_path), "launch", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    # Help text wraps; canonical phrases from the template should appear.
    assert "Default template" in result.stdout
    assert "shop-msg watch" in result.stdout
    assert "shop-msg pending inbox" in result.stdout
    # lead-ew86: the restored autonomous drain-AND-process directive is
    # surfaced in --help (the old 'await user direction' park directive is gone).
    assert "Implementer->Reviewer" in result.stdout
    assert "work_done" in result.stdout


# ---------------------------------------------------------------------------
# CLI default-resolution behavior (acceptance criteria 1 and 2)
# ---------------------------------------------------------------------------

class _RecordingController:
    """Stand-in controller that records the launch() kwargs it was called with."""

    def __init__(self):
        self.calls: list[dict] = []

    def launch(self, **kwargs):
        self.calls.append(kwargs)
        from bc_launcher.controller import CommandResult
        return CommandResult(exit_code=0, stdout="", stderr="")


@pytest.fixture
def recording_controller(monkeypatch):
    """
    Replace BcContainerController in the cli module with a recording stub so
    we can assert on the startup_prompt value main() passes through, without
    needing a live or fake docker driver.
    """
    recorder = _RecordingController()

    def _factory(_driver):
        return recorder

    monkeypatch.setattr(cli_module, "BcContainerController", _factory)
    # The RealDockerDriver constructor is still called; stub it to a no-op.
    monkeypatch.setattr(cli_module, "RealDockerDriver", lambda: object())
    return recorder


def test_launch_without_startup_prompt_injects_default_imperative(recording_controller):
    """
    Acceptance criterion 1: `bc-container launch <bc>` with no
    --startup-prompt injects the default imperative (with bc_name
    substituted) into the controller.launch call.
    """
    exit_code = cli_main(["launch", "shopsystem-messaging"])
    assert exit_code == 0
    assert len(recording_controller.calls) == 1
    passed = recording_controller.calls[0]["startup_prompt"]
    expected = DEFAULT_STARTUP_PROMPT_TEMPLATE.format(bc_name="shopsystem-messaging")
    assert passed == expected
    # Sanity: substitution happened and load-bearing phrases are present.
    assert "shop-msg watch --bc shopsystem-messaging" in passed
    assert "shop-msg pending inbox --bc shopsystem-messaging" in passed
    # lead-ew86: the restored autonomous drain-AND-process-to-gated-work_done
    # directive replaces the regressed 'await user direction' park.
    assert "process each pending dispatch" in passed
    assert "Implementer->Reviewer" in passed
    assert "work_done" in passed
    assert "await user direction" not in passed
    assert "{bc_name}" not in passed


def test_launch_with_explicit_startup_prompt_is_total_override(recording_controller):
    """
    Acceptance criterion 2: `bc-container launch <bc> --startup-prompt 'foo'`
    injects exactly 'foo' — the override is total, not concatenated with
    the default, and no {bc_name}-substitution is applied to user input.
    """
    exit_code = cli_main(
        ["launch", "shopsystem-messaging", "--startup-prompt", "foo"]
    )
    assert exit_code == 0
    passed = recording_controller.calls[0]["startup_prompt"]
    assert passed == "foo"
    # Affirmatively assert NO default-template content leaked in (lead-ew86:
    # the distinctive default content is now the autonomous drain-AND-process
    # directive, not the retired 'await user direction' park).
    assert "shop-msg watch" not in passed
    assert "shop-msg pending inbox" not in passed
    assert "process each pending dispatch" not in passed
    assert "work_done" not in passed


def test_launch_with_explicit_empty_string_startup_prompt_is_total_override(
    recording_controller,
):
    """
    Edge case for 'override is total': --startup-prompt '' produces an
    empty string at the controller layer, NOT the default template.
    (The controller's `if startup_prompt:` check then skips injection,
    which is the documented behavior of empty-string falsiness — this
    test pins that --startup-prompt '' does not fall back to the default.)
    """
    exit_code = cli_main(
        ["launch", "shopsystem-messaging", "--startup-prompt", ""]
    )
    assert exit_code == 0
    passed = recording_controller.calls[0]["startup_prompt"]
    assert passed == ""


# ---------------------------------------------------------------------------
# End-to-end via FakeDockerDriver: confirm the default text reaches tmux
# ---------------------------------------------------------------------------

def test_launch_default_prompt_reaches_tmux_send_keys(monkeypatch, tmp_path):
    """
    End-to-end (controller + fake driver): when launched via the CLI with
    no --startup-prompt, the default imperative reaches the tmux send-keys
    command issued inside the container.
    """
    # Set up credential dirs / files the controller checks for.
    credential_home = tmp_path / "fake_home"
    credential_home.mkdir()
    (credential_home / ".claude").mkdir()
    (credential_home / ".config" / "gh").mkdir(parents=True)
    (credential_home / ".gitconfig").write_text("")
    monkeypatch.setenv("HOME", str(credential_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: credential_home))

    # Provide a manifest so the network-resolution path succeeds.
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text(
        'product: shopsystem product\n'
        'bcs:\n'
        '  - name: shopsystem-messaging\n'
        '    remote: https://github.com/shopsystem/shopsystem-messaging.git\n'
        '    role: bc\n'
    )
    monkeypatch.chdir(tmp_path)

    fake_driver = FakeDockerDriver()

    def _factory(_real_driver):
        return BcContainerController(fake_driver)

    monkeypatch.setattr(cli_module, "BcContainerController", _factory)
    monkeypatch.setattr(cli_module, "RealDockerDriver", lambda: object())

    exit_code = cli_main(["launch", "shopsystem-messaging"])
    assert exit_code == 0

    expected_prompt = DEFAULT_STARTUP_PROMPT_TEMPLATE.format(
        bc_name="shopsystem-messaging"
    )

    # Post-lead-lez1 two-call shape: the prompt text reaches its OWN send-keys
    # invocation (text alone, no Enter), and a SEPARATE bare-Enter invocation
    # immediately follows it.  A single invocation carrying both text and Enter
    # is the retired paste-absorption regression, so the prompt-text invocation
    # must NOT carry Enter.
    text_calls = [
        c for c in fake_driver.exec_calls
        if c.command[:2] == ["tmux", "send-keys"]
        and expected_prompt in c.command
    ]
    assert text_calls, (
        "Expected a tmux send-keys carrying the default imperative.\n"
        f"Recorded exec calls: {[c.command for c in fake_driver.exec_calls]!r}"
    )
    text_call = text_calls[-1]
    assert "Enter" not in text_call.command, (
        "Post-lead-lez1: the prompt-text send-keys must NOT also carry Enter "
        "(that is the retired single-invocation paste-absorption shape).\n"
        f"Prompt-text invocation: {text_call.command!r}"
    )
    # The Enter must arrive as its own discrete invocation right after the text.
    send_keys = [
        c.command for c in fake_driver.exec_calls
        if c.command[:2] == ["tmux", "send-keys"]
    ]
    text_idx = send_keys.index(text_call.command)
    assert text_idx + 1 < len(send_keys) and send_keys[text_idx + 1] == [
        "tmux", "send-keys", "-t", "agent", "Enter"
    ], (
        "Expected a discrete bare-Enter send-keys immediately after the "
        f"prompt-text invocation.\nAll send-keys: {send_keys!r}"
    )
