"""Step definitions: tmux-DEFAULT autonomous engage restoration
(lead-ew86, @scenario_hash:e811193fc061e1e8 — ADR-050 D3 / ADR-018 D1-D2).

The ADR-050 --orchestrator split regressed the tmux DEFAULT engage to
arm-watcher+drain-then-"await user direction". This binding pins the RESTORED
autonomous default: the default startup prompt the launcher injects on the
tmux-default path must DIRECT drain-AND-process of each pending dispatch
through the Implementer->Reviewer loop to a Reviewer-gated work_done, with no
human-injected "go" between the drain and the work_done.

FIDELITY: the step defs drive the REAL launcher (controller.launch over the
FakeDockerDriver, resolving the default startup prompt exactly as
bc_launcher.cli.main() does when no --startup-prompt is supplied) and bind to
its ACTUAL recorded tmux `agent` send-keys — the injected default startup
prompt — never to a model. The assertions read the injected prompt token
recovered from the recorded send-keys, so a shallow no-op edit does not pass:
the "await user direction" park directive must be ABSENT and the
drain-AND-process-to-gated-work_done directive must be PRESENT in the same
injected prompt.
"""
from __future__ import annotations

from pytest_bdd import given, when, then, parsers

from tests.conftest import (
    _CADR_LAUNCH_PATH_TMUX,
    _cadr_build_parser,
)
from tests.support.container import (
    _cadr_tmux_agent_send_keys,
    _cadr_write_manifest,
)


def _ew86_injected_startup_prompt(ctx):
    """Recover the injected default startup prompt from the launcher's ACTUAL
    recorded tmux `agent` send-keys. The prompt reaches its own send-keys
    invocation (text alone, no Enter). It is identified — without re-deriving
    the template constant — as the agent send-keys token that carries the
    load-bearing drain directive `shop-msg pending inbox --bc <bc_name>`; the
    FULL recovered token is what the directive assertions then read."""
    bc_name = ctx["ew86_bc_name"]
    drain_needle = f"shop-msg pending inbox --bc {bc_name}"
    for call in _cadr_tmux_agent_send_keys(ctx):
        for tok in call.command:
            if drain_needle in tok:
                return tok
    raise AssertionError(
        "no injected default startup prompt found in the launcher's recorded "
        "tmux `agent` send-keys carrying "
        f"{drain_needle!r}; recorded send-keys: "
        f"{[c.command for c in _cadr_tmux_agent_send_keys(ctx)]!r}"
    )


@given(parsers.parse(
    'the container "{container_name}" is launched on the DEFAULT '
    '"--orchestrator tmux" engage with no explicit interactive-startup '
    "override supplied"))
def ew86_launch_tmux_default_no_override(
    container_name, ctx, fake_driver, controller, tmp_path
):
    """Drive the REAL launcher on the tmux-DEFAULT path with NO
    --startup-prompt and NO --orchestrator flag, so BOTH the tmux default and
    the DEFAULT startup-prompt resolution are exercised exactly as
    bc_launcher.cli.main() runs them."""
    from bc_launcher.cli import DEFAULT_STARTUP_PROMPT_TEMPLATE

    bc_name = container_name[len("bc-"):] if container_name.startswith("bc-") \
        else container_name

    # Parse the canonical CLI surface with NO override flags: this is the
    # tmux-default, no-interactive-startup-override engage.
    parser = _cadr_build_parser()
    args = parser.parse_args(["launch", bc_name])
    assert args.orchestrator == "tmux", (
        "no --orchestrator flag must default to tmux (the DEFAULT engage)"
    )
    assert getattr(args, "startup_prompt", None) is None, (
        "no --startup-prompt supplied means the DEFAULT startup prompt is "
        "resolved (no explicit interactive-startup override)"
    )
    launch_path = (
        _CADR_LAUNCH_PATH_TMUX
        if (args.orchestrator == "tmux" and not getattr(args, "fabro_path", False))
        else None
    )
    assert launch_path == _CADR_LAUNCH_PATH_TMUX

    # Resolve the DEFAULT startup prompt EXACTLY as cli.main() does when
    # --startup-prompt is omitted (explicit is None -> template substitution).
    startup_prompt = DEFAULT_STARTUP_PROMPT_TEMPLATE.format(bc_name=bc_name)

    manifest_path = _cadr_write_manifest(tmp_path, bc_name)
    result = controller.launch(
        bc_name=bc_name,
        repo_url=f"https://github.com/shopsystem/{bc_name}.git",
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
        startup_prompt=startup_prompt,
        launch_path=launch_path,
    )
    assert result.exit_code == 0, (
        f"tmux-default launch failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    ctx["cadr_result"] = result
    ctx["cadr_driver"] = fake_driver
    ctx["ew86_bc_name"] = bc_name
    ctx["container_name"] = container_name


@given(parsers.parse(
    '"shop-msg pending inbox --bc {bc_name}" already lists one or more '
    'dispatched work_ids (for example an "assign_scenarios" or a '
    '"request_maintenance") that arrived before the engage started'))
def ew86_pending_dispatches_exist(bc_name, ctx):
    """Precondition premise: the BC has pending dispatched inbox work waiting
    when the engage starts. This is the agent-runtime state the injected
    default prompt must be DIRECTED to process; at launcher altitude it is the
    scenario premise, recorded so the drain/process directive assertions read
    against it."""
    assert bc_name == ctx["ew86_bc_name"], (
        "the pending-inbox premise must name the same BC that was launched"
    )
    ctx["ew86_pending_dispatches"] = True


@when("the tmux-default engage arms its watcher and drains the pending inbox")
def ew86_engage_arms_and_drains(ctx):
    """Recover the injected default startup prompt from the launcher's ACTUAL
    recorded tmux send-keys — the directive the engage issues to arm the
    watcher and drain the inbox."""
    ctx["ew86_injected_prompt"] = _ew86_injected_startup_prompt(ctx)


@then(
    "the engage does NOT merely LIST the pending dispatches and then hold "
    'awaiting a human "go", but proceeds to PROCESS each pending dispatch '
    "through the normal Implementer->Reviewer loop")
def ew86_processes_not_parks(ctx):
    prompt = ctx["ew86_injected_prompt"]
    # The regressed park directive must be ABSENT: the engage must not "await
    # user direction" / hold for a human "go" after listing.
    assert "await user direction" not in prompt, (
        "the injected default startup prompt still parks with 'await user "
        f"direction'; it must not hold for a human 'go': {prompt!r}"
    )
    # The restored autonomous directive must be PRESENT: process each pending
    # dispatch through the Implementer->Reviewer loop.
    assert "process each pending dispatch" in prompt, (
        "the injected default startup prompt must DIRECT processing each "
        f"pending dispatch, not merely listing them: {prompt!r}"
    )
    assert "Implementer" in prompt and "Reviewer" in prompt, (
        "the injected default startup prompt must name the Implementer->"
        f"Reviewer loop as the processing path: {prompt!r}"
    )


@then(parsers.parse(
    'each processed dispatch reaches a Reviewer-gated "work_done" emitted on '
    "its scenario path, with NO human-injected \"go\" keystroke required "
    "between the drain and the work_done"))
def ew86_reaches_gated_work_done_no_human_go(ctx):
    prompt = ctx["ew86_injected_prompt"]
    # The directive terminates at a Reviewer-gated work_done.
    assert "work_done" in prompt, (
        "the injected default startup prompt must direct processing TO a "
        f"work_done, not stop at a drain: {prompt!r}"
    )
    # NO human "go" is required between drain and work_done: the SAME injected
    # prompt carries BOTH the drain directive and the process-to-work_done
    # directive, so nothing waits for a human keystroke in between. Bind to the
    # recorded send-keys: after the prompt + its Enter, the launcher injects NO
    # further "go" prompt of its own.
    bc_name = ctx["ew86_bc_name"]
    assert f"shop-msg pending inbox --bc {bc_name}" in prompt, (
        "the drain directive and the process-to-work_done directive must live "
        "in the SAME injected prompt so no human 'go' is needed between them: "
        f"{prompt!r}"
    )
    agent_prompt_tokens = [
        tok
        for call in _cadr_tmux_agent_send_keys(ctx)
        for tok in call.command
        if "await user direction" in tok
    ]
    assert agent_prompt_tokens == [], (
        "the launcher must inject NO 'await user direction' park prompt on the "
        f"tmux-default path; found {agent_prompt_tokens!r}"
    )


@then(parsers.parse(
    "after the engage settles every pending dispatched work_id has a "
    'corresponding "work_done" in the BC outbox and NONE of those dispatches '
    "remains stuck pending in the BC inbox"))
def ew86_all_dispatches_settle_to_work_done(ctx):
    prompt = ctx["ew86_injected_prompt"]
    # The autonomous directive covers EVERY pending dispatch to completion
    # (drain-AND-process to work_done), not a drain-then-park that leaves
    # dispatches stuck pending. Absence of the park directive + presence of the
    # each-dispatch process-to-work_done directive is the launcher-altitude
    # realization of "none remains stuck pending".
    assert "await user direction" not in prompt
    assert "process each pending dispatch" in prompt
    assert "work_done" in prompt, (
        "the injected default startup prompt must direct EVERY pending "
        f"dispatch through to a work_done: {prompt!r}"
    )
