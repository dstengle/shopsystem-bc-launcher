"""Structural teeth for lead-i0wi — THREE fabro-launcher residuals found
dogfooding fabro on real work.  The loop fail-closed CORRECTLY (ADR-051 held)
but blocked at the first LLM node; all three fixes PRESERVE the fail-closed
guarantee and the ADR-051 invariants.

Each test binds to the REAL artifact
(test-fidelity-for-image-layer-container-runtime-scenarios): F1/F2 parse the
committed ``workflow.fabro`` (via the launcher's own def-asset loader), and F3
drives the REAL launcher over the FakeDockerDriver and inspects the ACTUAL
recorded fabro engage exec — never a model.

F1 (BLOCKING) — classify off sonnet onto haiku + retry/backoff on judgment
    nodes.  A sonnet 429 on the agent-vault wire must NOT deterministically
    fail-close the whole deliverable at node 1 (classify).  TEETH: revert
    classify to sonnet-only-no-retry -> RED.

F2 — do NOT consume-and-lose the dispatch on a fail-closed run.  The
    deliverable-side blocked report must NOT emit a de-pending
    ``shop-msg respond work_done --status blocked`` that permanently consumes
    the dispatch (leaving a re-run to see an empty inbox and idle); it must
    report the block WITHOUT consuming (non-consuming nudge) so the dispatch
    stays pending == retriable without lead re-dispatch.  TEETH: revert
    emit_blk to the consuming ``respond work_done --status blocked`` -> RED.

F3 (non-fatal) — ``launch --orchestrator fabro`` returns after engage.  The
    engage's ``fabro run`` must be issued DETACHED/backgrounded so the blocking
    ``docker exec`` (hence ``launch()``) returns after the run is engaged,
    mirroring the tmux path's detached ``tmux new-session -d`` return.  TEETH:
    make the ``fabro run`` engage synchronous/foreground-blocking -> RED.
"""
from __future__ import annotations

import re
from pathlib import Path

from bc_launcher.controller import (
    BcContainerController,
    _fabro_def_asset_root,
)
from tests.fake_driver import FakeDockerDriver


# ===========================================================================
# Helpers — read the REAL committed def (F1/F2) and drive the REAL launcher (F3)
# ===========================================================================

def _workflow_text() -> str:
    """The REAL committed workflow.fabro bytes (the placed def)."""
    return (_fabro_def_asset_root() / "workflow.fabro").read_text()


def _node_body(graph: str, name: str) -> str:
    """Return the ``name [ ... ]`` attribute body for a node, scanning the
    matching ``]`` quote-aware so a shell ``[ ... ]`` inside a script= string
    does not close the node early."""
    m = re.search(rf"(?m)^\s*{re.escape(name)}\s*\[", graph)
    assert m is not None, f"node {name!r} not found in workflow.fabro"
    i = m.end() - 1
    depth = 0
    inq = False
    j = i
    while j < len(graph):
        c = graph[j]
        if inq:
            if c == "\\":
                j += 2
                continue
            if c == '"':
                inq = False
        else:
            if c == '"':
                inq = True
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return graph[i + 1:j]
        j += 1
    raise AssertionError(f"unterminated node body for {name!r}")


def _stylesheet(graph: str) -> str:
    m = re.search(r'model_stylesheet="([^"]*)"', graph)
    assert m is not None, "model_stylesheet= not found in workflow.fabro"
    return m.group(1)


# ---- F3: real launcher engage exec -----------------------------------------

BC_NAME = "shopsystem-messaging"
WORK_ID = "lead-i0wi-work-3"
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
            and "fabro run" in c.command[2]
        ):
            return c.command[2]
    raise AssertionError("the fabro engage exec (server start + run) must exist")


# ===========================================================================
# F1 — classify resolves to haiku (not sonnet); judgment nodes carry retry.
# ===========================================================================

def test_f1_classify_node_routes_to_haiku_not_sonnet():
    """The light `classify` node (the FIRST LLM node) must resolve to
    claude-haiku-4-5, NOT claude-sonnet-4-5 — a sonnet 429 on the agent-vault
    wire must not deterministically fail-close the deliverable at node 1.

    Structurally: classify must NOT carry the `.coding` class (which the
    stylesheet pins to claude-sonnet-4-5); it carries the `.classify` class,
    and the stylesheet pins `.classify` to claude-haiku-4-5.

    TEETH: revert classify to `class="coding"` (sonnet) -> RED.
    """
    graph = _workflow_text()
    body = _node_body(graph, "classify")
    m = re.search(r'class="([^"]+)"', body)
    assert m is not None, f"classify must declare a class; body:\n{body}"
    cls = m.group(1)
    assert cls != "coding", (
        "classify must be routed OFF the `.coding` class (which pins "
        "claude-sonnet-4-5); a sonnet 429 would otherwise fail-close the whole "
        f"deliverable at node 1. classify class is {cls!r}."
    )
    assert cls == "classify", (
        f"classify must carry the `.classify` class (haiku); got {cls!r}."
    )
    sheet = _stylesheet(graph)
    m2 = re.search(r"\.classify\s*\{\s*model:\s*([A-Za-z0-9._-]+)", sheet)
    assert m2 is not None, (
        f"the model_stylesheet must define a `.classify` rule; sheet:\n{sheet}"
    )
    assert m2.group(1) == "claude-haiku-4-5", (
        "the `.classify` stylesheet rule must pin claude-haiku-4-5 so the light "
        f"classify node runs on haiku; got model {m2.group(1)!r}."
    )


def test_f1_classify_node_carries_retry():
    """classify carries a `retry=N` so a transient/429 retries with backoff
    rather than deterministically fail-closing on the first hiccup.

    TEETH: drop `retry=` from classify -> RED.
    """
    body = _node_body(_workflow_text(), "classify")
    m = re.search(r"\bretry=(\d+)", body)
    assert m is not None and int(m.group(1)) >= 1, (
        f"classify must carry retry=N (N>=1) for 429 resilience; body:\n{body}"
    )


def test_f1_judgment_nodes_carry_retry():
    """Every sonnet JUDGMENT node (`suff`, `plan`, `impl`, `review`, `impl_f`)
    carries `retry=N` so a transient/429 on the agent-vault wire retries with
    backoff instead of deterministically fail-closing the deliverable.

    TEETH: drop `retry=` from any judgment node -> RED.
    """
    graph = _workflow_text()
    missing = []
    for name in ("suff", "plan", "impl", "review", "impl_f"):
        body = _node_body(graph, name)
        m = re.search(r"\bretry=(\d+)", body)
        if not (m and int(m.group(1)) >= 1):
            missing.append(name)
    assert not missing, (
        "these judgment nodes must carry retry=N for 429 resilience so ONE "
        f"transient 429 does not fail-close the deliverable: {missing!r}"
    )


# ===========================================================================
# F2 — the fail-closed report does NOT consume-and-lose the dispatch.
# ===========================================================================

def test_f2_emit_blk_does_not_consume_the_dispatch_on_fail_closed():
    """The deliverable-side fail-closed report node `emit_blk` must NOT emit a
    de-pending `shop-msg respond work_done --status blocked`, which permanently
    consumes the dispatch (a re-run then sees an empty inbox and idles — the
    fail-closed dispatch is LOST, needing lead re-dispatch).  It must instead
    report the block WITHOUT consuming, so the dispatch stays pending ==
    retriable without lead re-dispatch (defer-consume-until-terminal-complete /
    re-queue-on-block).

    TEETH: revert emit_blk to `shop-msg respond work_done --status blocked`
    (consume-on-fail-closed) -> RED.
    """
    body = _node_body(_workflow_text(), "emit_blk")
    # The consuming response (the residual) must be GONE from emit_blk.
    assert "respond work_done" not in body, (
        "emit_blk must NOT emit a de-pending `shop-msg respond work_done "
        "--status blocked` on the fail-closed path — that permanently consumes "
        "and LOSES the dispatch (a re-run sees an empty inbox and idles). Report "
        f"the block WITHOUT consuming instead. emit_blk body:\n{body}"
    )
    assert "--status blocked" not in body, (
        "emit_blk must not write a consuming blocked work_done response; "
        f"body:\n{body}"
    )
    # It must still REPORT the block (ADR-051: never silent) via a NON-consuming
    # channel — the lead->BC nudge, which does NOT write an outbox response and
    # therefore leaves the dispatch pending (retriable).
    assert "shop-msg nudge" in body, (
        "emit_blk must still REPORT the block (ADR-051 fail-closed is never "
        "silent) via a NON-consuming `shop-msg nudge` so the dispatch stays "
        f"pending == retriable. emit_blk body:\n{body}"
    )
    # (the $WORK_ID reference is escaped inside the outer script="..." string)
    assert "--work-id" in body and "$WORK_ID" in body, (
        "the non-consuming block report must reference the dispatched "
        f"$WORK_ID; emit_blk body:\n{body}"
    )


def test_f2_reviewer_stays_sole_scenario_path_complete_emitter():
    """ADR-051 preserved under F2: the F2 change to emit_blk must NOT introduce
    a NEW gated work_done(complete) emitter.  The only complete-emitters stay
    the pre-F2 baseline — `emit_r` (reviewer, scenario path) and `emit_f`
    (implementer, flat maintenance/empty-bugfix path); emit_blk is NOT among
    them.  (ky63 LEG 2 separately pins that emit_r is the SOLE emitter reachable
    on the *scenario* success path.)

    TEETH: add a `bc-emit work-done ... --status complete` to emit_blk (or any
    other node) -> RED.
    """
    graph = _workflow_text()
    complete_emitters = []
    for name in re.findall(r"(?m)^\s*([A-Za-z_]\w*)\s*\[", graph):
        if name == "graph":
            continue
        body = _node_body(graph, name)
        if (
            "bc-emit" in body
            and "work-done" in body
            and "--status complete" in body
        ):
            complete_emitters.append(name)
    assert sorted(complete_emitters) == ["emit_f", "emit_r"], (
        "the gated work_done(complete) emitters must stay the pre-F2 baseline "
        "{emit_r (reviewer/scenario path), emit_f (implementer/flat path)}; the "
        "F2 change must NOT add a new complete-emitter. found: "
        f"{complete_emitters!r}"
    )
    # emit_blk (the fail-closed report) is NOT a complete-emitter.
    assert "emit_blk" not in complete_emitters, (
        "emit_blk must never emit a work_done(complete) — it is the fail-closed "
        "block report (ADR-051: no false complete)."
    )


# ===========================================================================
# F3 — launch --orchestrator fabro returns after engage (run is detached).
# ===========================================================================

def test_f3_fabro_run_engage_is_detached_so_launch_returns(tmp_path):
    """The engage's `fabro run` must be issued DETACHED/backgrounded so the
    BLOCKING `docker exec` (driver.exec_run is a synchronous subprocess.run)
    returns after the run is ENGAGED — mirroring the tmux path, which issues a
    detached `tmux new-session -d` that returns immediately.  Previously the
    engage script ENDED with a FOREGROUND `fabro run`, so `docker exec` (hence
    `launch()`) never returned.

    Structural pin: `fabro run` is inside a backgrounded brace group
    `{ nohup ... & }` and the script does NOT end with a foreground `fabro run`.

    TEETH: make the `fabro run` engage synchronous/foreground-blocking (drop the
    `nohup ... &` around it) -> RED.
    """
    script = _engage_script(tmp_path)
    # `fabro run` is present (scn 77 pin) ...
    assert "fabro run" in script, (
        f"the engage must still ISSUE `fabro run`; script:\n{script}"
    )
    # ... and it is DETACHED inside a `{ nohup ... fabro run ... & }` group so
    # the exec returns promptly after engaging.
    m = re.search(r"\{\s*nohup [^}]*fabro run[^}]*&\s*\}", script)
    assert m is not None, (
        "the `fabro run` engage must be BACKGROUNDED inside a `{ nohup ... & }` "
        "group so the blocking `docker exec` returns after the run is engaged "
        f"(mirroring the tmux detached-session return); script:\n{script}"
    )
    # The script must NOT end with a FOREGROUND `fabro run` (the residual bug):
    # after stripping a trailing newline, the last non-space token sequence is
    # the backgrounded group's closing `}`, not a bare `fabro run ...`.
    tail = script.rstrip()
    assert tail.endswith("}"), (
        "the engage script must END with the backgrounded run group's `}` (the "
        "run is detached), NOT a foreground `fabro run` that blocks the exec; "
        f"script tail:\n{tail[-120:]!r}"
    )


def test_f3_server_start_argv_still_issued(tmp_path):
    """F3 detaching changes HOW the engage is issued, NOT whether: the
    `fabro server start --foreground --no-web` argv (scn 77 pin,
    @scenario_hash:68e14cdcd8b7c145) stays present in the engage script.

    TEETH: drop the server-start argv -> RED.
    """
    script = _engage_script(tmp_path)
    assert "fabro server start --foreground --no-web" in script, (
        "scn 77 pins `fabro server start --foreground --no-web` in the engage; "
        f"detaching must not drop it. script:\n{script}"
    )


def test_f3_env_before_install_ordering_intact(tmp_path):
    """F3 must keep esy4 Defect D intact: the three exports still PRECEDE
    `fabro install` after the detach change.

    TEETH: reorder an export after `fabro install` -> RED.
    """
    script = _engage_script(tmp_path)
    install_pos = script.find("fabro install")
    assert install_pos != -1
    for token in ("SSL_CERT_FILE=", "ANTHROPIC_API_KEY=", "ANTHROPIC_BASE_URL="):
        pos = script.find(token)
        assert pos != -1 and pos < install_pos, (
            f"esy4 Defect D: {token!r} must precede `fabro install` after the "
            f"F3 detach change; script:\n{script}"
        )
