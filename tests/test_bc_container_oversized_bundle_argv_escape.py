"""
pytest-bdd binding for the oversized-content-blob argv-escape scenarios
(lead-m4zt).

Pins the BLOCKER bugfix: the launcher must place a large content blob — the
fabro def-bundle (fabro path) or the startup prompt (tmux path) — into the
container WITHOUT carrying it as a single argv element to docker exec/run, so
a blob larger than the Linux MAX_ARG_STRLEN per-argument limit (128 KiB) does
not fail the spawn with E2BIG ("Argument list too long") and the BC comes
online. Both placement sites are exercised.

Step definitions live in tests/steps/oversized_argv.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_oversized_bundle_argv_escape.feature")
