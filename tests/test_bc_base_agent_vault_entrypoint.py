"""pytest-bdd binding for the bc-base agent-vault entrypoint feature (bclaunch-9rr).

BC-INTERNAL structural hardening: parses the committed bc-base Dockerfile +
CA-trust entrypoint/profile script CONTENT (docker build is NOT run — docker is
unavailable in this environment), asserting agent-vault is installed with a
version pin, the CA is materialized from AGENT_VAULT_CA_PEM to the fixed
container path with the five trust vars exported (durable for exec/login shells
via /etc/profile.d), and the placeholder .credentials.json is baked into the
image. See the feature file for the full rationale.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_base_agent_vault_entrypoint.feature")
