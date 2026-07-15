"""pytest-bdd binding for the launch-time active-LLM-provider resolution
(lead-ifye3.2).

Behavior 1 (@scenario_hash:1d9d3777e3c3d8f5): a plain launch with no
operator-supplied provider override keeps the Anthropic-subscription path as
the active LLM provider and requests no OpenRouter agent-vault credential.

FIDELITY: the step defs (tests/steps/llm_provider.py) drive the REAL launcher
(controller.launch over the FakeDockerDriver) on the --orchestrator fabro path
and bind to its ACTUAL recorded engage exec — never a model.
"""
import pytest
from pytest_bdd import scenarios

scenarios("../features/bc_container_llm_provider_override.feature")


def test_behavior5_runtime_single_model_resolution_honest_skip():
    """The "every node resolves to that same single run-wide model AT RUNTIME"
    leg of @scenario_hash:a3b2b6bebcee78f5 needs a REAL `fabro run` against a live
    OpenRouter (via the openrouter-shim + agent-vault broker) to observe the
    per-node resolved model — unavailable in the unit env.  Honest SKIP, never
    faked.  The launcher-fidelity half (a single run-wide --model/--provider, no
    -I MODEL_* inputs, and no per-node-class model_stylesheet templating) IS
    proven by the a3b2b6bebcee78f5 BDD scenario."""
    pytest.skip(
        "real `fabro run` against live OpenRouter needed to observe every "
        "node-class resolving the SAME run-wide model at runtime "
        "(honest SKIP, never faked)"
    )


def test_behavior6_live_end_to_end_completion_honest_skip():
    """The TRUE live end-to-end completion leg of @scenario_hash:5d49031bab379ba6
    — a real dispatch actually reaching a gated work_done — needs a real OpenRouter
    key + a live agent-vault broker + fabro>=0.267 executing the finite `fabro run`
    through the openrouter-shim to a real work_done.  That infrastructure is NOT
    present in-session.  Honest SKIP, never faked.  The achievable fidelity (the
    `.coding` run-wide model resolving to a literal OpenRouter model ID via the
    openrouter-shim base_url, and the gated-work_done terminal emit_r being
    reachable) IS proven by the 5d49031bab379ba6 BDD scenario.

    lead-ifye3.12: this docstring tracked the capstone through its retirement
    lineage 76badc67216f0d91 -> 1cee6978cbf9ac53 -> 5d49031bab379ba6; it had gone
    stale by two retirements, naming a hash no longer live in this register.

    Note the live retry is additionally gated on TWO in-flight precondition fixes
    the successor now names as Givens — lead-ifye3.6 (shop-templates' stale
    model_stylesheet pour) and lead-ifye3.10 (this BC's provider-identity call
    site).  The Architect initiates that retry once both land.
    """
    pytest.skip(
        "real OpenRouter key + live agent-vault broker + fabro>=0.267 needed to "
        "observe a real dispatch reaching a gated work_done end-to-end through the "
        "openrouter-shim (honest SKIP, never faked)"
    )


def test_fabro_run_provider_flag_carries_registered_identity_not_active_name(
    monkeypatch, ctx, fake_driver, controller, tmp_path
):
    """DEFECT B (work_id lead-ifye3.10): the finite `fabro run --provider` flag on
    the OpenRouter override launch path must carry fabro's REGISTERED provider
    IDENTITY (``FABRO_OPENROUTER_PROVIDER_IDENTITY`` == "openai" — the NATIVE
    entry the override registers, with base_url pointed at the openrouter-shim),
    NOT the operator-facing active provider NAME ("openrouter").

    fabro resolves `--provider` by LITERAL lookup against its configured
    providers.  Scenario af07c326a031fafe pins that the override registers the
    native "openai" identity and that "no new custom 'openrouter' fabro provider
    is registered" — so `--provider openrouter` names a provider that, BY THAT
    SAME PIN, cannot exist.  The run dies before its first node with:

        Precondition failed: Provider "openrouter" is not configured

    (lead-lp4us observed exactly this, and observed the identical run with
    `--provider openai` clear the precondition and execute real nodes.)

    FIDELITY: drives the REAL launcher over the FakeDockerDriver and reads the
    ACTUAL recorded finite `fabro run` argv — never a model, never a string the
    test itself authored.

    NOTE: this unit test binds to the REGISTERED-IDENTITY end state.  The live
    pin a3b2b6bebcee78f5 currently pins the CONTRADICTORY `--provider openrouter`
    in its Then-clause verbatim; that conflict is reported to the lead in this
    work_id's work_done rather than resolved by silently amending the scenario.
    """
    from bc_launcher.fabro.constants import FABRO_OPENROUTER_PROVIDER_IDENTITY
    from tests.steps.llm_provider import _engage_script, _model_provider_flags
    from tests.support.container import _odd9_drive_fabro_launch

    monkeypatch.setenv("BCLAUNCHER_LLM_PROVIDER", "openrouter")
    _odd9_drive_fabro_launch(
        "shopsystem-messaging", ctx, fake_driver, controller, tmp_path,
        work_id=None,
    )
    script = _engage_script(ctx)
    _model, provider = _model_provider_flags(script)

    assert provider is not None, (
        "the openrouter-override launch must record a finite `fabro run` "
        f"carrying a `--provider` flag; engage script:\n{script}"
    )
    # The flag must be the identity fabro is actually REGISTERED under, sourced
    # from the same constant the settings-block writer uses (one source of truth).
    assert provider == FABRO_OPENROUTER_PROVIDER_IDENTITY, (
        "the finite `fabro run` must carry `--provider "
        f"{FABRO_OPENROUTER_PROVIDER_IDENTITY}` (fabro's REGISTERED native "
        f"provider identity), got --provider {provider!r}. A `--provider` naming "
        "the operator-facing active provider name fails fabro's literal provider "
        'lookup: Precondition failed: Provider "openrouter" is not configured.'
    )
    # Negative control: the operator-facing NAME must not reach the flag.
    assert provider != "openrouter", (
        "`--provider openrouter` names a provider af07c326a031fafe pins as never "
        "registered; the run dies before its first node"
    )
