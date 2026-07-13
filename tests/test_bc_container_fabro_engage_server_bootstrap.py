"""pytest-bdd binding for the reshaped clone-path server-config bootstrap re-pin
(402241f3f31cecd9, lead-1vbw / ADR-058 AMENDMENT-3).

The external agent-free watcher supervisor STILL needs the clone-path
server-config bootstrap: a fresh clone-path container has no host-home ~/.fabro,
so before starting the ONE per-container fabro server the launcher provisions a
VALID ~/.fabro/settings.toml (DISTINCT from the project
/workspace/.fabro/settings.toml) and runs the watcher's finite `fabro run
workflow.fabro` children with cwd=/workspace/.fabro so the poured def resolves.

FIDELITY: the step defs (tests/steps/fabro_watch.py + the reused odd9 server-
config/settings steps) drive the REAL launcher over the FakeDockerDriver and bind
to its ACTUAL recorded engage exec — never a model.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_fabro_engage_server_bootstrap.feature")
