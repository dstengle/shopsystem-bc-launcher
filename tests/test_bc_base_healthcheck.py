"""pytest-bdd binding for the bc-base HEALTHCHECK feature (bclaunch-wuo).

BC-INTERNAL structural hardening: parses the committed bc-base Dockerfile +
bc-healthcheck.sh probe-script CONTENT (docker build is NOT run — docker is
unavailable in this environment), asserting the image declares a real
HEALTHCHECK that runs the probe script, and that the probe TCP-checks the
agent-vault broker (via the in-container HTTPS_PROXY address) and the messaging
database (via SHOPMSG_DSN). The assertions target the ACTUAL directive and the
ACTUAL probe targets, so a no-op or wrong-target HEALTHCHECK fails. See the
feature file for the full rationale (closes the fake-only health gap behind
lead scenario 3b2a81c1bfe2897e).
"""
from pytest_bdd import scenarios

scenarios("../features/bc_base_healthcheck.feature")
