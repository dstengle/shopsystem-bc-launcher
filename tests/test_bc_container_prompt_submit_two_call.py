"""
pytest-bdd test module for the two-discrete-invocation send-keys shape
(lead-lez1 / lead-9q0f).

Binds features/bc_container_prompt_submit_two_call.feature (scenarios 30 and
31), which pin the root-cause fix: a prompt is committed to the agent's input
loop only when issued as TWO discrete tmux send-keys invocations — the prompt
text alone first, then a bare Enter second — never as a single invocation
carrying both (which Claude Code's TUI absorbs as a paste, swallowing the CR).

Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_prompt_submit_two_call.feature")
