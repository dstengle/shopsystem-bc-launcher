"""pytest-bdd binding for the fabro orchestrator launch-path
anthropic-oauth-shim wiring pin (lead-vwib, @scenario_hash:8b5a1b9e5499293b).

LAUNCHER WIRING ONLY. The anthropic-oauth-shim is lead-so2h's owned artifact —
a REAL stdlib ThreadingHTTPServer reverse proxy committed at
docker/bc-base/anthropic-oauth-shim (baked into bc-base v0.3.44 at
/usr/local/bin/anthropic-oauth-shim). This scenario pins the LAUNCHER WIRING
for the fabro orchestrator launch path: the launcher starts that baked shim
in-container on 127.0.0.1:8788 and writes fabro's effective settings so
[llm.providers.anthropic] base_url points at the shim with adapter "anthropic"
(native format, no translation — ADR-049 D2), while the native fabro vault
stays __PLACEHOLDER__-only (ADR-049 D1).

FIDELITY (test-fidelity-for-image-layer-container-runtime-scenarios):
* LISTENER leg EXECUTES the REAL committed so2h shim
  (`anthropic-oauth-shim --host 127.0.0.1 --port 8788`), confirms it genuinely
  BINDS + listens (TCP connect succeeds), then stops it; AND asserts the
  launcher's fabro-path start argv targets that mode + host + port.
* BASE_URL leg parses the REAL fabro settings the launcher WROTE and asserts
  base_url == http://127.0.0.1:8788/v1 + adapter == anthropic.
* VAULT leg asserts the committed def's vault stays __PLACEHOLDER__-only and no
  real cred is written into the settings/shim config on this path.

Step definitions live in tests/conftest.py (lead-vwib block).
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_fabro_path_oauth_shim_wiring.feature")
