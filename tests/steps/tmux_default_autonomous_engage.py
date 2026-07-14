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


# ---------------------------------------------------------------------------
# Behavior 2 (lead-ew86, @scenario_hash:f65d43b1d8704f28): the tmux-DEFAULT
# autonomous engage is BOUNDED to DISPATCHED inbox work — it emits a work_done
# solely for a dispatched work_id and synthesizes NO unrequested follow-on
# work. Same launcher altitude as behavior 1: the guardrail is realized as a
# directive encoded in the injected default startup prompt, recovered from the
# launcher's ACTUAL recorded tmux `agent` send-keys (never from a model). The
# assertions read the recovered prompt token, so a shallow no-op edit does not
# pass: the dispatched-work bound and the no-unrequested-follow-on directive
# must BOTH be present in the same injected prompt.
# ---------------------------------------------------------------------------


@given(parsers.parse(
    'the container "{container_name}" is launched on the DEFAULT '
    '"--orchestrator tmux" engage'))
def ew86b_launch_tmux_default(
    container_name, ctx, fake_driver, controller, tmp_path
):
    """Drive the REAL launcher on the tmux-DEFAULT path with NO --startup-prompt
    (reusing behavior 1's launch machinery), so the DEFAULT startup prompt is
    resolved exactly as bc_launcher.cli.main() resolves it and its ACTUAL
    recorded tmux send-keys carry the injected default prompt this scenario
    then reads the dispatched-work guardrail out of."""
    ew86_launch_tmux_default_no_override(
        container_name, ctx, fake_driver, controller, tmp_path
    )


@given(parsers.parse(
    "the BC inbox lists exactly the dispatched work_ids present at engage "
    "and no others"))
def ew86b_inbox_lists_exactly_dispatched(ctx):
    """Precondition premise: at engage the inbox holds exactly the DISPATCHED
    work_ids and no others. At launcher altitude this is the scenario premise
    the injected default prompt's dispatched-work bound is asserted against —
    the runtime inbox contents are the agent-runtime's concern, out of this
    Python codebase's reach."""
    assert ctx.get("ew86_bc_name"), (
        "the inbox premise must follow a completed tmux-default launch"
    )
    ctx["ew86b_inbox_only_dispatched"] = True


@when(parsers.parse(
    "the tmux-default engage drains and processes its pending inbox work "
    "autonomously to work_done"))
def ew86b_engage_drains_and_processes(ctx):
    """Recover the injected default startup prompt from the launcher's ACTUAL
    recorded tmux send-keys — the directive that drains and autonomously
    processes the pending inbox work to work_done."""
    ctx["ew86_injected_prompt"] = _ew86_injected_startup_prompt(ctx)


@then(parsers.parse(
    'every "work_done" the engage emits corresponds to a work_id that was '
    "dispatched into the inbox, so the autonomy is bounded to dispatched work"))
def ew86b_work_done_bounded_to_dispatched(ctx):
    prompt = ctx["ew86_injected_prompt"]
    # The guardrail: the injected prompt must BOUND the autonomy to DISPATCHED
    # inbox work — a work_done is emitted SOLELY for a dispatched work_id. This
    # is more than "process each pending dispatch" (behavior 1); it names the
    # dispatched-work-only bound so a work_done cannot correspond to an
    # undispatched work_id.
    assert "dispatched inbox work only" in prompt, (
        "the injected default startup prompt must BOUND the autonomy to the "
        f"dispatched inbox work only: {prompt!r}"
    )
    assert "solely for a work_id that was dispatched" in prompt, (
        "the injected default startup prompt must direct a work_done SOLELY "
        f"for a dispatched work_id: {prompt!r}"
    )
    # The bound is realized on top of behavior 1's autonomous directive, not in
    # place of it: the process-to-work_done directive is still present and the
    # regressed park directive still absent.
    assert "process each pending dispatch" in prompt
    assert "work_done" in prompt
    assert "await user direction" not in prompt


@then(parsers.parse(
    'the engage emits NO "work_done" for any work_id that was not dispatched '
    "into the inbox, synthesizing no unrequested follow-on work beyond what "
    "was dispatched"))
def ew86b_no_unrequested_follow_on(ctx):
    prompt = ctx["ew86_injected_prompt"]
    # The complementary guardrail: the injected prompt must direct that NO
    # unrequested follow-on work is synthesized beyond what was dispatched —
    # so no work_done is emitted for an undispatched work_id.
    assert "synthesize no unrequested follow-on work" in prompt, (
        "the injected default startup prompt must direct that NO unrequested "
        f"follow-on work is synthesized: {prompt!r}"
    )
    assert "beyond what was dispatched" in prompt, (
        "the no-unrequested-follow-on directive must be scoped to 'beyond what "
        f"was dispatched', bounding the autonomy to dispatched work: {prompt!r}"
    )


# ---------------------------------------------------------------------------
# Behavior 3 (lead-ew86, @scenario_hash:cdaaf8d986398b36): autonomous
# drain-and-process is the tmux DEFAULT, while the operator-driven /
# await-direction interactive session is a DISTINCT, explicitly-selected
# NON-default mode reached ONLY via an explicit --startup-prompt override.
#
# This scenario pins the DISTINCTION between the two modes. It binds BOTH
# observables to the launcher's ACTUAL recorded tmux `agent` send-keys, driving
# the REAL launcher (controller.launch over the FakeDockerDriver) TWICE and
# resolving each prompt EXACTLY as bc_launcher.cli.main() does:
#   (1) NO --startup-prompt  -> the DEFAULT template (autonomous drain-and-
#       process directive) is resolved and injected verbatim; no operator "go"
#       is required.
#   (2) an explicit --startup-prompt selecting an operator-driven /
#       await-direction session -> that operator prompt is a TOTAL override
#       (no substitution) injected VERBATIM, and the autonomous default
#       directive is ABSENT — the two modes are distinct and separately
#       selected.
# The override launch runs on its own fresh FakeDockerDriver so its recorded
# send-keys are isolated from the default launch's.
#
# The autonomous default half is already delivered (behaviors 1 & 2 / the
# DEFAULT template) and the override half is already delivered by the existing
# TOTAL-override mechanism in cli.main(); this scenario PINS that already-present
# capability. The autonomous-default marker asserted here is the load-bearing
# `process each pending dispatch` directive.
# ---------------------------------------------------------------------------

_EW86C_OPERATOR_PROMPT = (
    "Operator-driven interactive session for {bc}: arm Monitor on "
    "shop-msg watch --bc {bc}, then AWAIT operator direction — do NOT process "
    "the inbox autonomously; hold until the operator explicitly types go."
)

# The load-bearing autonomous-default directive marker (from the DEFAULT
# startup-prompt template). Its presence marks the autonomous drain-and-process
# mode; its absence marks a non-autonomous (operator-driven) mode.
_EW86C_AUTONOMOUS_MARKER = "process each pending dispatch"


def _ew86c_agent_send_keys_tokens(driver):
    """Every token across the launcher's ACTUAL recorded tmux `agent` send-keys
    for ``driver``. Binds assertions to the launcher's real recorded output."""
    tokens = []
    for call in _cadr_tmux_agent_send_keys({"cadr_driver": driver}):
        tokens.extend(call.command)
    return tokens


@when(parsers.parse(
    'the container "{container_name}" is launched on the DEFAULT '
    '"--orchestrator tmux" engage with no explicit interactive-startup '
    "override supplied"))
def ew86c_when_launch_default_and_prepare_override(
    container_name, ctx, fake_driver, controller, tmp_path
):
    """Drive the REAL launcher TWICE to establish the two-mode comparison this
    scenario pins:

    (a) the DEFAULT no-override tmux engage (reusing behavior 1's launch
        machinery), resolving the DEFAULT startup prompt EXACTLY as
        bc_launcher.cli.main() does when --startup-prompt is omitted; and
    (b) the DISTINCT explicitly-selected NON-default mode: an explicit
        --startup-prompt that selects an operator-driven / await-direction
        interactive session, resolved EXACTLY as cli.main() does (explicit ->
        TOTAL override, no substitution) and driven on a FRESH driver so its
        recorded send-keys are isolated from (a)'s."""
    from bc_launcher.cli import DEFAULT_STARTUP_PROMPT_TEMPLATE
    from bc_launcher.controller import BcContainerController
    from tests.fake_driver import FakeDockerDriver

    # (a) DEFAULT no-override launch.
    ew86_launch_tmux_default_no_override(
        container_name, ctx, fake_driver, controller, tmp_path
    )
    bc_name = ctx["ew86_bc_name"]
    ctx["ew86c_default_driver"] = fake_driver
    ctx["ew86c_default_prompt"] = DEFAULT_STARTUP_PROMPT_TEMPLATE.format(
        bc_name=bc_name
    )

    # (b) explicit-override launch selecting the operator-driven session.
    operator_prompt = _EW86C_OPERATOR_PROMPT.format(bc=bc_name)
    parser = _cadr_build_parser()
    args = parser.parse_args(
        ["launch", bc_name, "--startup-prompt", operator_prompt]
    )
    assert args.orchestrator == "tmux", (
        "the override launch must remain on the DEFAULT tmux engage — only the "
        "startup-prompt (interactive vs autonomous) differs between the modes"
    )
    explicit = getattr(args, "startup_prompt", None)
    assert explicit is not None, (
        "an explicit --startup-prompt must be present to select the "
        "operator-driven NON-default mode"
    )
    # Resolve EXACTLY as cli.main(): explicit prompt is a TOTAL override; no
    # template substitution occurs.
    resolved_override = explicit

    ovr_driver = FakeDockerDriver()
    ovr_controller = BcContainerController(
        ovr_driver, monotonic=ovr_driver.monotonic
    )
    manifest_path = _cadr_write_manifest(tmp_path, bc_name)
    ovr_result = ovr_controller.launch(
        bc_name=bc_name,
        repo_url=f"https://github.com/shopsystem/{bc_name}.git",
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
        startup_prompt=resolved_override,
        launch_path=_CADR_LAUNCH_PATH_TMUX,
    )
    assert ovr_result.exit_code == 0, (
        f"override tmux launch failed: stdout={ovr_result.stdout!r} "
        f"stderr={ovr_result.stderr!r}"
    )
    ctx["ew86c_override_driver"] = ovr_driver
    ctx["ew86c_override_prompt"] = resolved_override


@then(parsers.parse(
    "the autonomous drain-and-process behavior is the DEFAULT for the tmux "
    'engage, beginning to process dispatched inbox work with no operator "go" '
    "required"))
def ew86c_autonomous_is_default(ctx):
    driver = ctx["ew86c_default_driver"]
    prompt = ctx["ew86c_default_prompt"]
    tokens = _ew86c_agent_send_keys_tokens(driver)
    # Observable (1): the DEFAULT no-override tmux engage injects the autonomous
    # default startup prompt VERBATIM into the launcher's ACTUAL recorded agent
    # send-keys.
    assert prompt in tokens, (
        "the DEFAULT no-override tmux engage must inject the autonomous default "
        f"startup prompt into the recorded agent send-keys; recorded: {tokens!r}"
    )
    # It DIRECTS processing dispatched inbox work (not merely listing) ...
    assert _EW86C_AUTONOMOUS_MARKER in prompt, (
        "the DEFAULT injected prompt must DIRECT processing each pending "
        f"dispatch (the autonomous default): {prompt!r}"
    )
    assert "Reviewer-gated work_done" in prompt, (
        "the DEFAULT injected prompt must direct processing TO a Reviewer-gated "
        f"work_done: {prompt!r}"
    )
    # ... with NO operator "go" required (the regressed park directive absent).
    assert "without waiting for a human go" in prompt, (
        "the DEFAULT injected prompt must require no human 'go' between drain "
        f"and work_done: {prompt!r}"
    )
    assert "await user direction" not in prompt, (
        "the DEFAULT injected prompt must NOT park awaiting operator direction: "
        f"{prompt!r}"
    )


@then(parsers.parse(
    "the await-direction / operator-driven interactive behavior is NOT the "
    "tmux default and is reached ONLY by an explicit interactive-startup "
    "override (for example an explicit startup-prompt that selects an "
    "operator-driven session)"))
def ew86c_await_direction_not_default(ctx):
    default_prompt = ctx["ew86c_default_prompt"]
    override_prompt = ctx["ew86c_override_prompt"]
    # await-direction is NOT the tmux default: the DEFAULT injected prompt
    # carries the autonomous directive and does NOT await operator direction.
    assert _EW86C_AUTONOMOUS_MARKER in default_prompt, (
        "the tmux DEFAULT must be the autonomous mode, not await-direction: "
        f"{default_prompt!r}"
    )
    assert "AWAIT operator direction" not in default_prompt, (
        "the tmux DEFAULT must NOT be the await-direction / operator-driven "
        f"mode: {default_prompt!r}"
    )
    # await-direction IS reached — ONLY — via the explicit interactive-startup
    # override: the operator-driven session lives in the explicitly-supplied
    # override prompt and nowhere in the default path.
    assert "AWAIT operator direction" in override_prompt, (
        "the operator-driven / await-direction session must be selected by the "
        f"explicit interactive-startup override: {override_prompt!r}"
    )
    assert override_prompt != default_prompt, (
        "the explicitly-selected operator-driven mode must be a DISTINCT prompt "
        "from the autonomous default"
    )


@then(parsers.parse(
    "when that explicit interactive override IS supplied the engage runs the "
    "operator-driven interactive session instead of autonomously processing "
    "the inbox, confirming the two modes are distinct and separately selected"))
def ew86c_override_runs_operator_session(ctx):
    driver = ctx["ew86c_override_driver"]
    override_prompt = ctx["ew86c_override_prompt"]
    tokens = _ew86c_agent_send_keys_tokens(driver)
    # Observable (2): the explicit interactive override is injected VERBATIM
    # into the override launch's ACTUAL recorded agent send-keys.
    assert override_prompt in tokens, (
        "the explicit interactive override must be injected VERBATIM into the "
        f"recorded agent send-keys; recorded: {tokens!r}"
    )
    # ...and the autonomous drain-and-process default directive is ABSENT on the
    # override launch — the engage runs the operator-driven session INSTEAD of
    # autonomously processing the inbox (the two modes are distinct and
    # separately selected).
    assert all(_EW86C_AUTONOMOUS_MARKER not in tok for tok in tokens), (
        "an explicit interactive override must NOT also inject the autonomous "
        f"drain-and-process default directive; recorded: {tokens!r}"
    )
    # The override runs the operator-driven / await-direction session.
    assert "AWAIT operator direction" in override_prompt, (
        "the override engage must run the operator-driven / await-direction "
        f"session: {override_prompt!r}"
    )
