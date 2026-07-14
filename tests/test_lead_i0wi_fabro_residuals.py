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


def _stylesheet_input_placeholder(sheet: str, cls: str) -> str | None:
    """The node-class INPUT PLACEHOLDER name a stylesheet rule resolves its
    `model:` from (lead-ifye3.2 behavior 4 converted the baked literals to
    `{{ inputs.MODEL_* }}` placeholders resolved via `-I` at fabro-run time)."""
    m = re.search(
        rf"\.{re.escape(cls)}\s*\{{\s*model:\s*\{{\{{\s*inputs\.([A-Za-z0-9_]+)\s*\}}\}}",
        sheet,
    )
    return m.group(1) if m else None


def _resolved_default_model(input_name: str) -> str:
    """The literal model ID the given MODEL_* input placeholder resolves to on
    the DEFAULT (anthropic / no-override) path: the workflow.toml [run.inputs]
    default (what `fabro validate` and a bare run render) — which must equal the
    Anthropic mapping-table row so the default launch is behavior-preserving."""
    import tomllib

    from bc_launcher.fabro.llm_provider import (
        LLM_PROVIDER_ANTHROPIC,
        resolve_model_mapping,
    )

    toml_path = _fabro_def_asset_root() / "workflow.toml"
    inputs = tomllib.loads(toml_path.read_text()).get("run", {}).get("inputs", {})
    toml_default = inputs.get(input_name)
    # The mapping-table tier the placeholder feeds (MODEL_CODING->coding etc.).
    tier = {"MODEL_CODING": "coding", "MODEL_REVIEW": "review",
            "MODEL_DEFAULT": "default"}[input_name]
    row_model = resolve_model_mapping(LLM_PROVIDER_ANTHROPIC)[tier]
    assert toml_default == row_model, (
        f"the workflow.toml default for {input_name} ({toml_default!r}) must "
        f"equal the Anthropic mapping row's {tier!r} model ({row_model!r}) so a "
        "default/validate run is behavior-equivalent to the launcher's resolved "
        "-I input"
    )
    return row_model


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


def _engage_call(tmp_path: Path):
    """Drive the REAL launcher on the fabro path and return the recorded
    ExecCall that carries the fabro engage (server start + run)."""
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
            return c
    raise AssertionError("the fabro engage exec (server start + run) must exist")


def _engage_script(tmp_path: Path) -> str:
    return _engage_call(tmp_path).command[2]


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
    # lead-ifye3.2 behavior 4: the `.classify` rule's model is no longer a baked
    # literal — it is the MODEL_DEFAULT node-class input placeholder (classify
    # folds into the DEFAULT tier), resolved to a literal model ID via the
    # provider-keyed mapping table.  The haiku-not-sonnet invariant is preserved:
    # MODEL_DEFAULT resolves (default/anthropic path) to claude-haiku-4-5.
    placeholder = _stylesheet_input_placeholder(sheet, "classify")
    assert placeholder == "MODEL_DEFAULT", (
        "the `.classify` stylesheet rule must resolve its model from the "
        "MODEL_DEFAULT input placeholder (classify folds into the default node-"
        f"class tier); sheet:\n{sheet}"
    )
    resolved = _resolved_default_model(placeholder)
    assert resolved == "claude-haiku-4-5", (
        "the `.classify` node-class model must resolve to claude-haiku-4-5 on "
        "the default path so the light classify node runs on haiku (sonnet-4-5 "
        f"is persistently 429 on the fleet); resolved {resolved!r}."
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
    """The engage must be issued DETACHED AT THE DOCKER LEVEL so the BLOCKING
    `docker exec` (driver.exec_run is a synchronous subprocess.run that reads
    the exec's stdout/stderr pipes to EOF) returns after the run is ENGAGED —
    mirroring the tmux path's detached-session return.

    lead-lwk4 R7 SUPERSEDES the ineffective v0.3.49 mechanism: the prior fix
    backgrounded `fabro run` INSIDE the script (`{ nohup ... & }`), but the
    backgrounded children INHERIT the exec's stdout/stderr pipes, so
    subprocess.run never sees EOF and `launch()` blocked anyway.  The real fix
    issues the engage exec via `docker exec -d` (detach=True): the docker daemon
    backgrounds the engage and the exec returns IMMEDIATELY without reading the
    exec's pipes, so the foreground fabro server's stdio never rides the
    launcher's pipes and `launch()` RETURNS.

    Structural pin: the recorded engage ExecCall carries detach=True, so the
    launcher cannot block on the foreground fabro server.  The engage SCRIPT is
    unchanged (docker-level detach), so scn 77 / esy4 pins stay green verbatim.

    TEETH: issue the engage exec synchronously (detach=False) -> RED.
    """
    call = _engage_call(tmp_path)
    script = call.command[2]
    # The engage still ISSUES `fabro run` (scn 77 pin) ...
    assert "fabro run" in script, (
        f"the engage must still ISSUE `fabro run`; script:\n{script}"
    )
    # ... and the engage exec is DETACHED at the docker level (`docker exec -d`),
    # so the blocking `docker exec` returns after engaging and `launch()` does
    # not hang on the foreground fabro server.
    assert call.detach is True, (
        "the fabro engage must be issued DETACHED (docker exec -d) so the "
        "blocking `docker exec` (which reads the exec pipes to EOF) returns "
        "after engaging — nohup-inside-the-script does NOT detach the child "
        f"stdio from the exec pipes (the v0.3.49 residual). call={call!r}"
    )
    # The engage exec stays a `/bin/sh -c` payload (scn 77 / esy4 matchers read
    # command[2] as a substring), unchanged by the docker-level detach.
    assert call.command[:2] == ["/bin/sh", "-c"], (
        f"the engage must stay a `/bin/sh -c` exec; got {call.command[:2]!r}"
    )
    # The foreground fabro server keeps running headless: it is backgrounded in
    # its own brace group WITHIN the (detached) engage script.
    assert re.search(r"\{\s*nohup [^}]*fabro server start[^}]*&\s*\}", script), (
        "the foreground `fabro server start` must be backgrounded inside the "
        f"engage so it keeps running headless; script:\n{script}"
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
