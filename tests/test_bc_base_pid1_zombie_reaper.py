"""pytest-bdd binding for the bc-base PID 1 reaping-init feature (lead-xnop).

BC-INTERNAL structural hardening: parses the committed bc-base Dockerfile
CONTENT (docker build is NOT run — docker is unavailable in this environment),
asserting `tini` is installed and the ENTRYPOINT is the tini reaping init in
exec form WRAPPING the agent-vault CA entrypoint script, so PID 1 reaps
orphaned <defunct> children (the 540-zombie bug) while CA materialization is
preserved. See the feature file for the full rationale.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_base_pid1_zombie_reaper.feature")
