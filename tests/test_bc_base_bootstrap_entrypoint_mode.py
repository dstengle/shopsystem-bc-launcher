"""pytest-bdd binding for the bc-base interactive bootstrap entrypoint mode
feature (lead-f6xs).

Structural inspection (docker build is NOT run — docker is unavailable in this
environment): parses the COMMITTED bootstrap-entrypoint script + bc-base
Dockerfile content, asserting the bootstrap mode invokes `claude` and
`gh auth login` interactively attached to the host TTY (NOT wrapped as
`agent-vault run -- claude`), places no "__PLACEHOLDER__" credential, ships as a
mode of the EXISTING bc-base lineage image (not a separate purpose-built
image), and resolves the four baked framework CLIs on PATH exactly as for a
brokered run. See the feature file for the full rationale.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_base_bootstrap_entrypoint_mode.feature")
