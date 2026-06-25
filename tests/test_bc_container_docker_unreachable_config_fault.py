"""pytest-bdd binding for the docker-unreachable config-fault scenarios
(lead-wdvx Bug 2: classify permission-denied / not-mounted as a
docker-unreachable config failure, distinct from a legitimate empty result).

Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_docker_unreachable_config_fault.feature")
