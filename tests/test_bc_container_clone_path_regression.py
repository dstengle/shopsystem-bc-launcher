"""
pytest-bdd binding for the bc-container launch clone-path regression guards
(lead-uiwu): manifest remote resolution + loud failure (FACET 1), /workspace
agent-user ownership before the clone (FACET 2), and broker MITM CA trusted
before the clone (FACET 3).

Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_clone_path_regression.feature")
