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
