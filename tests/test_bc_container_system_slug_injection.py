"""
pytest-bdd test module for SHOPMSG_SYSTEM_SLUG resolve+inject scenarios (lead-53y0).

Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_system_slug_injection.feature")
