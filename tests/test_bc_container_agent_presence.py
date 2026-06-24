"""
pytest-bdd test module for the lead-pixf agent-presence / infra-failure
scenarios (f2ddd6c7 / 010e776c / aeebb281).

The scenarios() call binds the feature file to this module's test session.
Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_agent_presence.feature")
