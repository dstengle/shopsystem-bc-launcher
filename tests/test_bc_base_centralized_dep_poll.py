"""pytest-bdd binding for the centralized scheduled bc-base dependency
check-bump-rebuild feature (lead-czwo)."""
from pytest_bdd import scenarios

scenarios("../features/bc_base_centralized_dep_poll.feature")
