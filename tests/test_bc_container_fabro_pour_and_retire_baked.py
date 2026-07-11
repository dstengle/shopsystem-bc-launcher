"""pytest-bdd binding for the fabro-def pour-delivery + baked-bundle-retirement
feature (lead-ona9, @scenario_hash:7700eea079ffe1d8).

Step definitions live in tests/steps/fabro_pour_retire.py."""
from pytest_bdd import scenarios

scenarios("../features/bc_container_fabro_pour_and_retire_baked.feature")
