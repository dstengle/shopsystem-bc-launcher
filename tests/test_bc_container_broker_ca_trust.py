"""
pytest-bdd binding for the broker-CA mount + TLS-trust env scenarios
(bclaunch-7pf).  Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_broker_ca_trust.feature")
