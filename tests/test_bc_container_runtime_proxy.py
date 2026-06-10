"""
pytest-bdd binding for the container RUNTIME HTTPS_PROXY derivation + precedence
scenarios (bclaunch-3q12).

Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_runtime_proxy.feature")
