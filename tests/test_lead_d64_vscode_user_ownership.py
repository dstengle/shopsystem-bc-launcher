"""
Unit tests pinning the lead-d64 vscode-user / ownership fix.

Background
----------
Post-lead-5ig the launcher was non-functional end-to-end because Claude
Code refuses ``--dangerously-skip-permissions`` as root ("cannot be used
with root/sudo privileges for security reasons").  The BC image's
default USER is root, so every ``docker exec`` inherited root.  Three
distinct ownership problems compounded:

  (1) tmux + Claude Code launched as root → Claude exits immediately.
  (2) ``git clone`` + ``bd dolt pull`` ran as root → ``/workspace``
      root-owned → vscode could not write afterward.
  (3) ``cp`` of host gitconfig and ``.claude.json`` ran as root → the
      destination files under ``/home/vscode/`` were root-owned →
      vscode's gh / git tooling broke.

This file pins the FakeDockerDriver-level invariants that fix all three:

  (a) Every tmux client exec_run (new-session, send-keys, capture-pane,
      has-session, attach-session) runs as ``-u vscode``.
  (b) A ``chown -R vscode:vscode /workspace`` exec_run exists and is
      issued AFTER the clone + bd dolt pull exec_runs and BEFORE the
      tmux new-session exec_run.
  (c) The cp gitconfig and cp .claude.json exec_runs do not produce
      root-owned files under /home/vscode — verified by asserting they
      either run as ``-u vscode`` or are followed by a chown that
      transfers ownership.

These tests use FakeDockerDriver, which now records the ``user`` kwarg
on every ExecCall.  No live Docker is required.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bc_launcher.controller import (
    AGENT_CONTAINER_USER,
    AGENT_TMUX_SESSION,
    BcContainerController,
    CONTAINER_WORKSPACE,
)
from tests.fake_driver import FakeDockerDriver


CONTAINER = "bc-shopsystem-messaging"
BC_NAME = "shopsystem-messaging"
VSCODE = "vscode"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_credential_home(tmp_path: Path) -> Path:
    """Build a credential_home dir with the three standard credential sources."""
    home = tmp_path / "fake_home"
    home.mkdir()
    (home / ".claude").mkdir()
    (home / ".claude" / ".claude.json").write_text("{}")
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


def _launch_with_clone(tmp_path: Path) -> FakeDockerDriver:
    """Run a normal launch (clone + tmux + claude) and return the driver."""
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    home = _make_credential_home(tmp_path)
    manifest = _make_manifest(tmp_path)
    result = controller.launch(
        bc_name=BC_NAME,
        repo_url="https://example.invalid/shopsystem-messaging.git",
        startup_prompt="anything",
        manifest_path=manifest,
        credential_home=home,
    )
    assert result.exit_code == 0, (
        f"launch failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    return driver


# ===========================================================================
# (a) Every tmux client exec_run runs as vscode
# ===========================================================================

def test_tmux_new_session_exec_run_runs_as_vscode(tmp_path):
    """
    Acceptance (lead-d64, fix scope 1): the ``tmux new-session`` exec_run
    must run as vscode.  Claude Code's --dangerously-skip-permissions
    check refuses to drop checks when EUID==0; running tmux as vscode is
    the only way Claude Code can subsequently start inside the session.
    """
    driver = _launch_with_clone(tmp_path)
    new_session_calls = [
        c for c in driver.exec_calls
        if c.command[:3] == ["tmux", "new-session", "-d"]
    ]
    assert new_session_calls, (
        f"Expected a tmux new-session exec_run.  "
        f"exec_calls: {[(c.command, c.user) for c in driver.exec_calls]!r}"
    )
    for call in new_session_calls:
        assert call.user == VSCODE, (
            f"tmux new-session must run as -u {VSCODE!r}; "
            f"got user={call.user!r} for command {call.command!r}"
        )


def test_all_tmux_send_keys_exec_runs_run_as_vscode(tmp_path):
    """
    Acceptance (lead-d64, fix scope 1, side effect): tmux refuses
    cross-user attach, so every send-keys against the vscode-owned
    session must also run as vscode.  This includes the claude-start
    send-keys, the bare-Enter trust-accept send-keys, and the
    startup-prompt send-keys.
    """
    driver = _launch_with_clone(tmp_path)
    send_keys_calls = [
        c for c in driver.exec_calls
        if c.command[:2] == ["tmux", "send-keys"]
    ]
    assert send_keys_calls, (
        f"Expected tmux send-keys exec_runs from launch.  "
        f"exec_calls: {[(c.command, c.user) for c in driver.exec_calls]!r}"
    )
    for call in send_keys_calls:
        assert call.user == VSCODE, (
            f"tmux send-keys must run as -u {VSCODE!r} "
            f"(tmux refuses cross-user attach against the vscode-owned "
            f"session created by `tmux new-session -u {VSCODE}`); "
            f"got user={call.user!r} for command {call.command!r}"
        )


def test_status_tmux_has_session_runs_as_vscode(tmp_path):
    """
    Acceptance (lead-d64, fix scope 1, side effect): ``status`` issues
    ``tmux has-session`` against the agent session.  Same cross-user
    constraint applies: the call must run as vscode.
    """
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    driver.set_running(CONTAINER, True)
    driver.add_tmux_session(CONTAINER, AGENT_TMUX_SESSION)

    controller.status(BC_NAME)

    has_session_calls = [
        c for c in driver.exec_calls
        if c.command[:3] == ["tmux", "has-session", "-t"]
    ]
    assert has_session_calls, (
        f"Expected a tmux has-session exec_run from status.  "
        f"exec_calls: {[(c.command, c.user) for c in driver.exec_calls]!r}"
    )
    for call in has_session_calls:
        assert call.user == VSCODE, (
            f"tmux has-session must run as -u {VSCODE!r}; "
            f"got user={call.user!r} for command {call.command!r}"
        )


def test_monitor_tmux_capture_pane_runs_as_vscode(tmp_path):
    """
    Acceptance (lead-d64, fix scope 1, side effect): ``monitor`` issues
    ``tmux capture-pane``.  Same constraint.
    """
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    driver.set_running(CONTAINER, True)
    driver.add_tmux_session(CONTAINER, AGENT_TMUX_SESSION)

    controller.monitor(BC_NAME)

    capture_calls = [
        c for c in driver.exec_calls
        if c.command[:3] == ["tmux", "capture-pane", "-p"]
    ]
    assert capture_calls, (
        f"Expected a tmux capture-pane exec_run from monitor.  "
        f"exec_calls: {[(c.command, c.user) for c in driver.exec_calls]!r}"
    )
    for call in capture_calls:
        assert call.user == VSCODE, (
            f"tmux capture-pane must run as -u {VSCODE!r}; "
            f"got user={call.user!r} for command {call.command!r}"
        )


def test_inject_tmux_send_keys_runs_as_vscode(tmp_path):
    """
    Acceptance (lead-d64, fix scope 1, side effect): the ``inject``
    subcommand issues ``tmux send-keys`` against the agent session.
    Same constraint as launch's send-keys: must run as vscode.
    """
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    driver.set_running(CONTAINER, True)

    controller.inject(BC_NAME, "probe text")

    send_keys_calls = [
        c for c in driver.exec_calls
        if c.command[:2] == ["tmux", "send-keys"]
    ]
    assert send_keys_calls, (
        f"Expected a tmux send-keys exec_run from inject.  "
        f"exec_calls: {[(c.command, c.user) for c in driver.exec_calls]!r}"
    )
    for call in send_keys_calls:
        assert call.user == VSCODE, (
            f"inject's tmux send-keys must run as -u {VSCODE!r}; "
            f"got user={call.user!r} for command {call.command!r}"
        )


def test_attach_tmux_attach_session_runs_as_vscode(tmp_path):
    """
    Acceptance (lead-d64, fix scope 1, side effect): ``attach`` uses
    ``exec_interactive`` to ``tmux attach-session``.  Cross-user attach
    is forbidden by tmux, so this must run as vscode.  The fake driver
    records ``user`` on interactive_calls the same way it records it on
    exec_calls.
    """
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    driver.set_running(CONTAINER, True)

    controller.attach(BC_NAME)

    attach_calls = [
        c for c in driver.interactive_calls
        if c.command[:3] == ["tmux", "attach-session", "-t"]
    ]
    assert attach_calls, (
        f"Expected a tmux attach-session interactive call from attach.  "
        f"interactive_calls: "
        f"{[(c.command, c.user) for c in driver.interactive_calls]!r}"
    )
    for call in attach_calls:
        assert call.user == VSCODE, (
            f"attach's tmux attach-session must run as -u {VSCODE!r}; "
            f"got user={call.user!r} for command {call.command!r}"
        )


# ===========================================================================
# (b) chown /workspace exec_run exists and is sequenced correctly
# ===========================================================================

def test_chown_workspace_exec_run_exists_after_clone_and_pull(tmp_path):
    """
    Acceptance (lead-d64, fix scope 2): a ``chown -R vscode:vscode
    /workspace`` exec_run must be issued, and its position in the
    exec_call sequence must be AFTER the clone + bd dolt pull and
    BEFORE the tmux new-session.

    The chown itself runs as root (the default — no ``-u`` flag), since
    transferring ownership of files vscode does not yet own requires
    root privileges.  Pinning ``user is None`` on the chown is therefore
    a positive invariant, not an oversight.
    """
    driver = _launch_with_clone(tmp_path)

    # Find each step's index in the exec_call sequence
    chown_idx = next(
        (
            i for i, c in enumerate(driver.exec_calls)
            if c.command[:2] == ["chown", "-R"]
            and CONTAINER_WORKSPACE in c.command
        ),
        None,
    )
    clone_idx = next(
        (
            i for i, c in enumerate(driver.exec_calls)
            if c.command[:2] == ["git", "clone"]
        ),
        None,
    )
    pull_idx = next(
        (
            i for i, c in enumerate(driver.exec_calls)
            if c.command[:3] == ["bd", "dolt", "pull"]
        ),
        None,
    )
    tmux_idx = next(
        (
            i for i, c in enumerate(driver.exec_calls)
            if c.command[:3] == ["tmux", "new-session", "-d"]
        ),
        None,
    )

    assert chown_idx is not None, (
        f"Expected a `chown -R ... {CONTAINER_WORKSPACE}` exec_run after "
        f"clone+pull (lead-d64 fix scope 2 — without it, /workspace is "
        f"root-owned and vscode cannot write).\n"
        f"exec_calls: {[(c.command, c.user) for c in driver.exec_calls]!r}"
    )
    assert clone_idx is not None and pull_idx is not None, (
        f"Expected clone + bd dolt pull exec_runs.  "
        f"exec_calls: {[c.command for c in driver.exec_calls]!r}"
    )
    assert tmux_idx is not None, (
        f"Expected tmux new-session exec_run.  "
        f"exec_calls: {[c.command for c in driver.exec_calls]!r}"
    )
    assert clone_idx < chown_idx, (
        f"chown /workspace (index {chown_idx}) must come AFTER git clone "
        f"(index {clone_idx}); otherwise the clone re-roots /workspace."
    )
    assert pull_idx < chown_idx, (
        f"chown /workspace (index {chown_idx}) must come AFTER bd dolt "
        f"pull (index {pull_idx}); otherwise the pull re-roots .beads."
    )
    assert chown_idx < tmux_idx, (
        f"chown /workspace (index {chown_idx}) must come BEFORE tmux "
        f"new-session (index {tmux_idx}); the tmux session's default cwd "
        f"is /workspace and the agent needs to write there immediately."
    )

    chown_call = driver.exec_calls[chown_idx]
    # The chown target must include vscode as both user and group.
    chown_target_spec = chown_call.command[2]
    assert (
        chown_target_spec == f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}"
    ), (
        f"chown must transfer ownership to "
        f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}; "
        f"got {chown_target_spec!r} in command {chown_call.command!r}"
    )
    # The chown itself must run as root (default — no -u flag) because
    # vscode cannot chown files it does not yet own.
    assert chown_call.user is None, (
        f"chown -R must run as root (default, user=None) so it can "
        f"transfer ownership of root-owned files left behind by clone/pull; "
        f"got user={chown_call.user!r}"
    )


def test_chown_workspace_recursive_flag_present(tmp_path):
    """
    Acceptance (lead-d64, fix scope 2): the chown must be recursive
    (``-R``) so .beads / nested directories are also transferred.  A
    non-recursive chown would leave /workspace's top dir vscode-owned
    but /workspace/.beads root-owned, reproducing the original defect
    one level deeper.
    """
    driver = _launch_with_clone(tmp_path)
    chown_calls = [
        c for c in driver.exec_calls
        if c.command and c.command[0] == "chown"
        and CONTAINER_WORKSPACE in c.command
    ]
    assert chown_calls, "No chown exec_run found targeting /workspace"
    for call in chown_calls:
        assert "-R" in call.command, (
            f"chown of {CONTAINER_WORKSPACE} must be recursive (-R) to cover "
            f"nested .beads / clone contents.  Got command: {call.command!r}"
        )


# ===========================================================================
# (c) credential-write exec_runs do not produce root-owned files under
#     /home/vscode
# ===========================================================================

# NOTE (ADR-026 / lead-v4ih / lead-hxb8): the two former tests here
# (test_cp_gitconfig_does_not_produce_root_owned_file_in_vscode_home and
# test_cp_claude_json_does_not_produce_root_owned_file_in_vscode_home) pinned
# the host-credential cp steps — copying /tmp/host-gitconfig into
# /home/vscode/.gitconfig and the host .claude.json into /home/vscode — which
# the agent-vault credential model RETIRES.  Under ADR-026 zero host-filesystem
# credential coupling reaches the container, so there is no such cp to assert
# vscode-ownership on.  The vscode-ownership invariant for the BC's OWN
# /workspace (the chown tests above) is unaffected and remains pinned.


# ===========================================================================
# Smoke: the AGENT_CONTAINER_USER constant matches the user we assert on
# ===========================================================================

def test_agent_container_user_constant_is_vscode():
    """
    The AGENT_CONTAINER_USER constant exists and resolves to ``vscode``.
    This pin guards against an accidental rename (e.g. to ``ubuntu``)
    that would silently re-introduce the lead-d64 defect by mismatching
    the user against the BC image's actual unprivileged account.
    """
    assert AGENT_CONTAINER_USER == VSCODE, (
        f"AGENT_CONTAINER_USER must be {VSCODE!r} to match the BC base "
        f"image's unprivileged user account; got "
        f"{AGENT_CONTAINER_USER!r}"
    )
