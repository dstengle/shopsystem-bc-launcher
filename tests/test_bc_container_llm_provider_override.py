"""pytest-bdd binding for the launch-time active-LLM-provider resolution
(lead-ifye3.2).

Behavior 1 (@scenario_hash:1d9d3777e3c3d8f5): a plain launch with no
operator-supplied provider override keeps the Anthropic-subscription path as
the active LLM provider and requests no OpenRouter agent-vault credential.

FIDELITY: the step defs (tests/steps/llm_provider.py) drive the REAL launcher
(controller.launch over the FakeDockerDriver) on the --orchestrator fabro path
and bind to its ACTUAL recorded engage exec — never a model.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_llm_provider_override.feature")
