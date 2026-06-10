"""
pytest-bdd binding for the agent-vault env-injection scenarios (bclaunch-5hi).

Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_agent_vault_env.feature")
