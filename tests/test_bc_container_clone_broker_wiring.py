"""
pytest-bdd binding for the launch-time auto-clone broker-wiring scenarios
(bclaunch-5fji).

Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_clone_broker_wiring.feature")
