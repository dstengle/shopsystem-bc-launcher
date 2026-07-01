"""pytest-bdd binding for the --orchestrator {tmux|fabro} engage-tier pins
(lead-cadr — S4, @scenario_hash:68e14cdcd8b7c145 / @scenario_hash:ee8f4803eb5342f0).

S4 formalizes the canonical `bc-container launch <bc> --orchestrator {tmux|fabro}`
surface (tmux the DEFAULT; the S3 --fabro-path flag remains a HIDDEN ALIAS) and
the fabro ENGAGE step. AFTER the readiness barrier passes (scenario 34) the
engage tier the launcher issues is selected by --orchestrator:

* --orchestrator fabro (68e14cdcd8b7c145): the launcher REPLACES the tmux/claude
  engage tier (ADR-050 D3) with the fabro run-graph entry — it starts an
  EPHEMERAL in-container fabro server in the FOREGROUND with no web UI bound to
  127.0.0.1 (`fabro server start --foreground --no-web`) and runs the placed
  ADR-051 loop def against it (`fabro run workflow.fabro -I BC_NAME=<bc> -I
  WORK_ID=<work_id>`) as the engage, starting NO tmux `agent` send-keys session
  and NO `claude` engage on that path.
* default (ee8f4803eb5342f0): with NO --orchestrator flag the orchestrator
  defaults to tmux; the launcher engages via the existing tmux `agent`
  send-keys path exactly as scenario 04, starting NO ephemeral fabro server and
  issuing NO `fabro run`.

FIDELITY (test-fidelity-for-image-layer-container-runtime-scenarios): the step
defs drive the REAL launcher (controller.launch over the FakeDockerDriver) and
bind to its ACTUAL recorded exec/send-keys calls — never a model. Step
definitions live in tests/conftest.py (lead-cadr block).
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_orchestrator_flag_engage_tier.feature")
