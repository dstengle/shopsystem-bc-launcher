"""
pytest-bdd test module for the workspace-mount and opt-in docker-socket
launch scenarios (lead-zxtk).

The scenarios() call binds the feature file to this module's test session.
Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_workspace_mount_and_docker_socket.feature")
