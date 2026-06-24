"""pytest-bdd binding for the bc-base / bc-lead default-USER-vscode feature
(lead-fwrx / lead-t3dy, scenario a4caf0477a74e4bc).

Structural inspection (docker is NOT available in this environment, so the
published-image `docker inspect` Config.User and `docker run --rm <image>
whoami` assertions cannot run live). The scenario is bound to the
buildable-artifact source of truth: it asserts that BOTH the bc-base and the
bc-lead Dockerfiles resolve to a final/effective ``USER vscode``
(Config.User=vscode), that the synthetic ~/.claude state is baked at
/home/vscode/.claude and owned by vscode, and that the
entrypoint/healthcheck/runtime-write paths are chowned/permissioned for vscode
(the ownership mechanics that make the observable hold when the container runs
as uid 1000). The live `docker inspect` / `whoami` on the PUBLISHED image is the
lead's post-release pull verification.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_base_default_user_vscode.feature")
