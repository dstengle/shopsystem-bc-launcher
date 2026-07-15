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
    """The TRUE live end-to-end completion leg of @scenario_hash:76badc67216f0d91
    — a real dispatch actually reaching a gated work_done — needs a real OpenRouter
    key + a live agent-vault broker + fabro>=0.267 executing the finite `fabro run`
    through the openrouter-shim to a real work_done.  That infrastructure is NOT
    present in-session.  Honest SKIP, never faked.  The achievable fidelity (the
    `.coding` run-wide model resolving to a literal OpenRouter model ID via the
    openrouter-shim base_url, and the gated-work_done terminal emit_r being
    reachable) IS proven by the 76badc67216f0d91 BDD scenario."""
    pytest.skip(
        "real OpenRouter key + live agent-vault broker + fabro>=0.267 needed to "
        "observe a real dispatch reaching a gated work_done end-to-end through the "
        "openrouter-shim (honest SKIP, never faked)"
    )
