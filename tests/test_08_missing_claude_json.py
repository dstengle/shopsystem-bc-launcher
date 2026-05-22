"""
pytest-bdd test module for scenario 6172b02f5f57d034:
bc-container launch warns to stderr and proceeds when host .claude.json is absent.

Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/08-missing-claude-json-warns-and-proceeds.feature")
