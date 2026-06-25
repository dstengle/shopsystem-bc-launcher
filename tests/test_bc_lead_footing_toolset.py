"""pytest-bdd binding for the bc-lead footing toolset feature (lead-ys8x;
scenarios c5edfa89da00af8a / 98a0683d0360349e / a0992b2156d132e3).

Structural inspection (docker is NOT available in this environment, so the
published-image `docker run --rm <image> docker compose version` / `dolt
version` / `command -v dolt` assertions cannot run live). The scenarios are
bound to the buildable-artifact source of truth: the committed
docker/bc-lead/Dockerfile must install the docker compose plugin
(docker-compose-plugin) AND install the dolt engine binary onto PATH. The live
`docker compose version` / `dolt version` on the REBUILT published bc-lead
image is the lead's post-release pull verification — consistent with how every
prior bc-base/bc-lead image-content scenario is gated in this BC
(a4caf0477a74e4bc, d9909f38abea83b5, the framework-CLI pins).
"""
from pytest_bdd import scenarios

scenarios("../features/bc_lead_footing_toolset.feature")
