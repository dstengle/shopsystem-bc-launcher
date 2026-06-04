"""pytest-bdd binding for the bc-base framework-CLI owner/repo pin feature.

BC-internal test-rigor hardening (bead shopsystem-bc-launcher-tuk). Structurally
asserts all five bc-base framework-CLI installs are pinned to their CORRECT
owner/repo, so a wrong-owner/wrong-repo (404-class) regression on any of the
five trips BDD. See the feature file for the full rationale.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_base_framework_cli_pins.feature")
