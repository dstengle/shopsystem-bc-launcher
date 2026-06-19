"""pytest-bdd binding for the lead-b14a multi-line AGENT_VAULT_CA_PEM
--env-file preservation scenario (@scenario_hash:eb92b4a40939973f).

A multi-line broker CA PEM supplied via --env-file must survive _parse_env_file
intact (not truncated at the first physical newline), travel into the container
env, and materialize byte-for-byte through the committed bc-base
docker/bc-base/agent-vault-ca.sh entrypoint (`printf '%s\n'`). Step definitions
live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_broker_ca_trust.feature")
