"""pytest-bdd binding for the bc-base fabro + anthropic-oauth-shim feature
(lead-ckq5).

bc-base bakes the fabro binary (pinned v0.254.0 from fabro-sh/fabro) and a
real stdlib-only anthropic-oauth-shim launcher, both present + launchable in
the running container; and the single centralized scheduled poll enrolls fabro
as a 6th baked dependency against fabro-sh/fabro (resolving with the workflow's
own GITHUB_TOKEN, bump-then-rebuilding :latest on a newer release).

FIDELITY: docker is unavailable in-env. The fabro leg of a3512aedb8763150 binds
to the docker/bc-base/Dockerfile install (comment-stripped detection); the live
`fabro --version` is the lead's pull verification, gated by the build-time
self-check. The anthropic-oauth-shim is a REAL committed file, so the test
EXECUTES the actual committed shim (`python3 <shim> --help`) and asserts exit 0
AND stdlib-only. The poll scenario binds to the committed workflow's executable
body via _strip_yaml_comments (5vyb comment-exclusion pattern).
"""
from pytest_bdd import scenarios

scenarios("../features/bc_base_fabro_and_oauth_shim.feature")
