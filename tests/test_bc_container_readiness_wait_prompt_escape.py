"""
pytest-bdd test module for readiness-wait interactive-prompt Escape handling
(lead-cw7m / lead-c713).

Binds features/bc_container_readiness_wait_prompt_escape.feature (scenarios
048607861da16ff4 / 815f8e470163f669 / acf59eb2e265fde7): during the readiness
wait (BEFORE the input-ready marker "bypass permissions on"), if the agent
pane presents an interactive prompt that is NOT the workspace-trust prompt and
blocks reaching input-ready, the launcher dismisses it with a DISCRETE tmux
send-keys carrying ONLY Escape (NEVER Enter / '1', so the fullscreen renderer
is NOT enabled), emits a host-discoverable WARNING naming the auto-dismissed
prompt, continues the readiness loop to input-ready, and injects the startup
prompt so the BC comes online.  The whole scan-dismiss loop is BOUNDED by the
existing 60s readiness timeout: when auto-dismissal never reaches input-ready
the launcher STOPS dismissing at 60s (no infinite loop), warns that the main
input did not become ready within 60 seconds, and proceeds WITHOUT injecting.

These scenarios EXTEND the lead-q3uy/gs03 engage-phase Esc-dismiss posture
(AFTER input-ready) to the READINESS-WAIT phase (BEFORE input-ready); they
COMPOSE with — and do NOT supersede — the lead-q3uy engage scenarios.

Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_readiness_wait_prompt_escape.feature")
