"""
pytest-bdd test module for engage blocking-option-screen Escape handling
(lead-q3uy).

Binds features/bc_container_engage_option_screen_escape.feature (scenarios
f68d8199fef70fa7 / f17f0fc747e44e47 / 91d4c1486c7b7d48): on engage the launcher
recognizes a blocking interactive option screen after the input-ready marker;
if it exposes an escape/dismiss affordance it sends a DISCRETE tmux send-keys
carrying ONLY Escape (never Enter) to dismiss it, captures the rendered screen
content, logs a host-discoverable WARNING, then submits the startup prompt
directly (no host-side inject).  A screen with NO escape affordance is NOT
auto-confirmed with Enter; the launch surfaces a WARNING naming it.

Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_engage_option_screen_escape.feature")
