"""
Unit tests pinning the lead-mf15 durable-vscode-ownership tightening.

Background
----------
lead-d64 / lead-ezzr established that the launcher chowns the ENTIRE
workspace to vscode during provisioning (a chown-whole-workspace-first
recipe plus defensive re-chowns before bd bootstrap and before the
shop-templates refresh).  Those chowns cover the files present AT
provisioning time.

lead-mf15 (BUG observed twice 2026-06-18): files under /workspace
intermittently become root-owned MID-RUN and block the vscode agent —
(1) .beads cloned root-owned at bring-up; (2) .git/objects/7e/ became
root-owned from a LATER root-context git op — each needing a host
`docker exec -u root chown -R 1000:1000`.  The existing chowns run
BEFORE the last root-context provisioning op (the shop-templates
refresh), so a path created or re-rooted by that later op (or any
root-context op after the last chown) re-introduces root ownership that
no subsequent chown corrects before the agent engages.

Durable fix (scenario @scenario_hash:d9e4ce60e03df361): assert vscode
ownership of the WHOLE workspace AFTER the last provisioning operation
that may write under /workspace as root, and immediately BEFORE the
agent (tmux new-session) starts — so the ownership snapshot the agent
sees is unconditionally vscode-owned regardless of any intermediate
re-root, and no host-side chown is ever needed.

These tests use FakeDockerDriver and require no live Docker.  They are
ADDITIVE: the chown-whole-workspace-first recipe and the
.beads-vscode-owned pin (2904f3a905567b48) must continue to hold (those
are pinned in test_lead_d64_vscode_user_ownership.py and
test_bc_container_beads_usable.py respectively).
"""
from __future__ import annotations

from pathlib import Path

from bc_launcher.controller import (
    AGENT_CONTAINER_USER,
    BcContainerController,
    CONTAINER_WORKSPACE,
)
from tests.fake_driver import FakeDockerDriver


BC_NAME = "shopsystem-messaging"
VSCODE = "vscode"


def _make_credential_home(tmp_path: Path) -> Path:
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


def _launch(tmp_path: Path) -> FakeDockerDriver:
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


def _recursive_workspace_vscode_chown(call) -> bool:
    """True iff ``call`` is `chown -R vscode:vscode /workspace` (as root)."""
    cmd = call.command
    if not cmd or cmd[0] != "chown":
        return False
    if "-R" not in cmd:
        return False
    if f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}" not in cmd:
        return False
    return any(a.rstrip("/") == CONTAINER_WORKSPACE for a in cmd)


def _last_provisioning_write_idx(driver) -> int:
    """Index of the LAST provisioning exec that may write under /workspace
    as a root-context operation — the shop-templates refresh (the final
    root-context provisioning step before the agent engages).

    The shop-templates `update` exec OVERWRITES `.claude/...` under
    /workspace and is the last provisioning op in the launch sequence; a
    path it creates after the prior chown is exactly the re-root the
    durable invariant must survive.
    """
    idx = None
    for i, c in enumerate(driver.exec_calls):
        if c.command[:2] == ["shop-templates", "update"]:
            idx = i
    return idx


def test_final_workspace_chown_runs_after_last_provisioning_op_and_before_tmux(
    tmp_path,
):
    """
    Acceptance (lead-mf15, @scenario_hash:d9e4ce60e03df361): a
    `chown -R vscode:vscode /workspace` exec_run must be issued AFTER the
    LAST root-context provisioning op (the shop-templates refresh) and
    BEFORE the tmux new-session.

    Without this final assertion, the last chown runs BEFORE the
    shop-templates refresh, so a path the refresh (or any later
    root-context op) creates/re-roots after that chown is left root-owned
    — the exact mid-run re-root that required a host docker exec chown
    twice on 2026-06-18.
    """
    driver = _launch(tmp_path)

    last_prov_idx = _last_provisioning_write_idx(driver)
    assert last_prov_idx is not None, (
        "Expected a shop-templates update exec_run (the last root-context "
        "provisioning op).  exec_calls: "
        f"{[(c.command, c.user) for c in driver.exec_calls]!r}"
    )

    tmux_idx = next(
        (
            i for i, c in enumerate(driver.exec_calls)
            if c.command[:3] == ["tmux", "new-session", "-d"]
        ),
        None,
    )
    assert tmux_idx is not None, (
        "Expected a tmux new-session exec_run.  exec_calls: "
        f"{[c.command for c in driver.exec_calls]!r}"
    )

    final_chown_idx = next(
        (
            i for i, c in enumerate(driver.exec_calls)
            if _recursive_workspace_vscode_chown(c)
            and i > last_prov_idx
            and i < tmux_idx
        ),
        None,
    )
    assert final_chown_idx is not None, (
        "Expected a `chown -R vscode:vscode /workspace` exec_run AFTER the "
        f"last provisioning op (index {last_prov_idx}: shop-templates "
        f"update) and BEFORE tmux new-session (index {tmux_idx}).  Without "
        "it, a path the refresh — or any later root-context op — "
        "creates/re-roots is left root-owned, blocking the vscode agent "
        "(the lead-mf15 mid-run re-root observed twice 2026-06-18).\n"
        f"exec_calls: {[(c.command, c.user) for c in driver.exec_calls]!r}"
    )

    final_chown = driver.exec_calls[final_chown_idx]
    assert final_chown.user is None, (
        "the final ownership-assertion chown must run as root (default, "
        "user=None) so it can transfer ownership of any root-owned path "
        f"left behind by a later root-context op; got user={final_chown.user!r}"
    )


def test_no_root_context_op_runs_under_workspace_after_the_final_chown(tmp_path):
    """
    Acceptance (lead-mf15, @scenario_hash:d9e4ce60e03df361): once the final
    ownership-assertion chown has run, NO subsequent exec_run before the
    agent (tmux new-session) may write under /workspace as a root-context
    operation — otherwise it could re-root a path after the assertion and
    re-introduce the defect.

    Concretely: every exec_run sequenced AFTER the final
    `chown -R vscode:vscode /workspace` and BEFORE tmux new-session must
    either be that tmux step itself or run as the vscode user (never as a
    root-context op that writes under /workspace).
    """
    driver = _launch(tmp_path)

    final_chown_idx = None
    for i, c in enumerate(driver.exec_calls):
        if _recursive_workspace_vscode_chown(c):
            final_chown_idx = i
    assert final_chown_idx is not None, (
        "Expected at least one `chown -R vscode:vscode /workspace` exec_run."
    )

    tmux_idx = next(
        (
            i for i, c in enumerate(driver.exec_calls)
            if c.command[:3] == ["tmux", "new-session", "-d"]
        ),
        None,
    )
    assert tmux_idx is not None and final_chown_idx < tmux_idx, (
        f"The final /workspace chown (index {final_chown_idx}) must precede "
        f"tmux new-session (index {tmux_idx})."
    )

    for i in range(final_chown_idx + 1, tmux_idx):
        call = driver.exec_calls[i]
        if call.user == AGENT_CONTAINER_USER:
            # A vscode-context op leaves any path it touches vscode-owned —
            # harmless to the invariant.
            continue
        # A root-context op (user is None) is only a problem if it WRITES
        # under an agent-touched workspace path (a read, e.g.
        # `cat /workspace/.claude/shop/type.md`, re-roots nothing).  A
        # root-context WRITE after the final ownership assertion would
        # re-root paths the agent then cannot modify.
        writes = FakeDockerDriver._paths_written_under(call.command)
        assert not writes, (
            f"exec_run at index {i} ({call.command!r}) is a root-context "
            f"WRITE (user={call.user!r}) under {sorted(writes)!r} AFTER the "
            f"final /workspace ownership assertion (index {final_chown_idx}) "
            f"and BEFORE the agent starts (tmux new-session index {tmux_idx}); "
            "it re-roots paths the vscode agent cannot modify, reintroducing "
            "the lead-mf15 defect.  Either move it before the final chown or "
            "run it as vscode."
        )
