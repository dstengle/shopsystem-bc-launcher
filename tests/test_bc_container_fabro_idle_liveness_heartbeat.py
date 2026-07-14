"""pytest-bdd binding for the lead-8hpz idle-but-live fabro liveness heartbeat
(@scenario_hash:a5ce1af45ade7444).

ADDITIVE bugfix extending the structural liveness pin e94a01b26ed6a4cc (ADR-050
D3). The `--orchestrator fabro` engage's ONLY always-resident process is
`shop-msg watch --bc <name>` — a LISTEN/NOTIFY event source that wakes ONLY on a
real message and NEVER per poll tick — so it advances NO bc_presence heartbeat
while the BC is idle-but-live. THE FIX: the always-resident supervisor UPSERTs
bc_presence on a bounded cadence MESSAGE-INDEPENDENTLY, so an idle-but-live BC
stays ONLINE and its container healthcheck reports healthy.

FIDELITY: the step defs (tests/steps/fabro_idle_heartbeat.py) drive the REAL
launcher over the FakeDockerDriver and bind to its ACTUAL recorded engage script
— never a model.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_fabro_idle_liveness_heartbeat.feature")
