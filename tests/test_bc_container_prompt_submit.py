"""
pytest-bdd test module for bc-container prompt-submit scenarios (lead-xsmn / lead-hyee).

Binds features/bc_container_prompt_submit.feature, which pins the resolution of
the --startup-prompt / inject submit bug: a prompt must be COMMITTED to the
agent's input loop (Enter as a discrete tmux send-keys key argument), not left
as an unsubmitted buffer entry (text with an appended '\\n').

Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_prompt_submit.feature")
