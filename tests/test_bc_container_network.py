"""
pytest-bdd test module for bc-container product-scoped Docker network naming scenarios.

Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_network.feature")
