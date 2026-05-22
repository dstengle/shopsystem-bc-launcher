"""
pytest-bdd test module for bc-container manifest scenarios.

Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_manifest.feature")
