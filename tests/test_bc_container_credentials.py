"""
pytest-bdd test module for bc-container credential propagation scenarios.

The scenarios() call binds the feature file to this module's test session.
Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_credentials.feature")
