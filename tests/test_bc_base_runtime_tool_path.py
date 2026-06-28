"""pytest-bdd binding for the bc-base runtime tool-PATH regression guard
(lead-h755).

Regression guard pinning a present-but-unpinned RUNTIME invariant: a launched
bc-base BC ALREADY has gh and agent-vault resolvable on PATH inside the running
container ("command -v gh" / "command -v agent-vault" exit zero and print an
executable path). docker is EXPLICITLY EXCLUDED (bc-base carries no docker CLI
by design; PDR-020 Addendum II; docker is bc-LEAD-only).

docker is unavailable in this environment, so the running container is modelled
through the FakeDockerDriver in-container exec model (the same idiom as other
launched-container runtime scenarios); the real observable is the lead's pull
verification for the published image. See the feature file for the full
rationale.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_base_runtime_tool_path.feature")
