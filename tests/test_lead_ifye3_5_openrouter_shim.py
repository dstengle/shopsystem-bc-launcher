"""lead-ifye3.5 behavior 3 — the committed `openrouter-shim` reverse-proxy asset
and its bc-base Dockerfile bake.

The openrouter override (behaviors 1-2) points fabro's native "openai" provider
base_url at a LOCAL loopback endpoint and LAUNCHES an `openrouter-shim` process
there.  THIS behavior builds the shim asset the launcher points at: an
unsandboxed, container-level reverse proxy that forwards the sandboxed node's
request UNCHANGED to OpenRouter's real API host, with NO header reshaping
(contrast the anthropic-oauth-shim, which strips x-api-key + rewrites auth).

FIDELITY (this file): STRUCTURAL / bake pins that do not need a network.

  * UPSTREAM DEFAULT — the committed shim's default upstream is
    "https://openrouter.ai/api".  The "/api" suffix is LOAD-BEARING: a real
    scout proved bare "https://openrouter.ai" hits OpenRouter's website 404
    page, not the API; "/api" + the OpenAI-compatible request path is the API.

  * NO BARE urllib ON THE OUTBOUND HOP — a real scout proved bare Python
    `urllib` is Cloudflare-BLOCKED (403 "Access denied ... Cloudflare") against
    openrouter.ai even with a correct credential, while `curl`'s TLS/HTTP
    fingerprint is NOT blocked.  The anthropic-oauth-shim uses urllib
    SUCCESSFULLY only because api.anthropic.com has no Cloudflare bot-detection
    — matching it is NOT sufficient for openrouter.ai.  So the outbound hop must
    NOT use bare `urllib.request.urlopen`; it uses a standard-fingerprint client
    (`curl` via subprocess).  This is a construction-time choice; the live
    Cloudflare leg cannot be exercised in-session (no real key/network) and is
    honest-deferred — but shipping bare urllib and claiming it is fine is the
    exact regression this pin forbids.

  * NO HEADER RESHAPING — the shim forwards the incoming `Authorization: Bearer`
    header UNCHANGED (unlike the anthropic-oauth-shim, which DROPs x-api-key +
    REWRITEs Authorization).  Structurally: the openrouter shim's request-drop
    set must NOT include `authorization`.

  * STDLIB-ONLY IMPORTS + `--help` — the shim imports only the Python standard
    library (curl is an external BINARY invoked via subprocess, not a python
    import), and `--help` exits zero.

  * DOCKERFILE BAKE — docker/bc-base/Dockerfile COPYs the committed
    `openrouter-shim` to /usr/local/bin/openrouter-shim and chmods it
    executable, mirroring how the anthropic-oauth-shim is baked; the launcher
    self-pin literal/ARG lockstep (lead-tzw4y / lead-klxi) is UNDISTURBED.

The forwarding BEHAVIOR itself (loopback listen + "/api"+path concatenation +
unchanged Authorization + streamed response) is exercised functionally against a
mock loopback upstream by the @scenario_hash:7f55b8ee9e092692 pytest-bdd
scenario (tests/steps/llm_provider.py).
"""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

from tests.conftest import _REPO_ROOT
from tests.support.base_image import (
    _committed_oauth_shim_path,
    _top_level_imported_modules,
)
from tests.support.common import _find_bc_base_dockerfile


_OPENROUTER_SHIM_NAME = "openrouter-shim"
_OPENROUTER_UPSTREAM = "https://openrouter.ai/api"


def _committed_openrouter_shim_path() -> Path | None:
    """The committed openrouter-shim file the bc-base Dockerfile COPYs onto PATH.
    Discovered by scanning the Dockerfile's build context for the file COPYd as
    /usr/local/bin/openrouter-shim, exactly like _committed_oauth_shim_path."""
    dockerfile = _find_bc_base_dockerfile()
    if dockerfile is None:
        return None
    ctx_dir = dockerfile.parent
    dtext = dockerfile.read_text()
    m = re.search(
        r"(?im)^\s*COPY\s+(\S+)\s+\S*/openrouter-shim\b", dtext
    )
    if m:
        candidate = ctx_dir / m.group(1)
        if candidate.is_file():
            return candidate
    candidate = ctx_dir / _OPENROUTER_SHIM_NAME
    if candidate.is_file():
        return candidate
    return None


def _load_openrouter_shim_module():
    """Import the committed openrouter-shim as a module (no main() runs on import
    — the shim guards execution behind `if __name__ == '__main__'`)."""
    shim = _committed_openrouter_shim_path()
    assert shim is not None, (
        "the committed openrouter-shim asset does not exist yet (bc-base "
        "Dockerfile must COPY docker/bc-base/openrouter-shim onto PATH)"
    )
    # The committed shim is an extensionless script, so spec_from_file_location
    # cannot infer a loader — supply a SourceFileLoader explicitly.
    loader = importlib.machinery.SourceFileLoader("openrouter_shim_under_test", str(shim))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------


def test_openrouter_shim_default_upstream_carries_api_suffix():
    mod = _load_openrouter_shim_module()
    assert getattr(mod, "UPSTREAM", None) == _OPENROUTER_UPSTREAM, (
        "the openrouter-shim's default UPSTREAM must be "
        f"{_OPENROUTER_UPSTREAM!r} — the '/api' suffix is REQUIRED (bare "
        "'https://openrouter.ai' hits the website 404, not the API); got "
        f"{getattr(mod, 'UPSTREAM', None)!r}"
    )


def test_openrouter_shim_outbound_hop_does_not_use_bare_urllib():
    shim = _committed_openrouter_shim_path()
    assert shim is not None, "the committed openrouter-shim asset does not exist yet"
    src = shim.read_text()

    # The outbound hop must NOT use bare urllib (Cloudflare-blocks openrouter.ai);
    # it uses a standard-fingerprint client — curl via subprocess.
    assert "curl" in src, (
        "the openrouter-shim outbound hop must use a standard-fingerprint client "
        "(curl via subprocess) — bare urllib is Cloudflare-BLOCKED against "
        "openrouter.ai; no 'curl' reference found in the shim"
    )
    imported = _top_level_imported_modules(src)
    assert "subprocess" in imported, (
        "the openrouter-shim must invoke its outbound client via `subprocess` "
        f"(the curl hop); imported top-level modules: {sorted(imported)!r}"
    )

    # Bare urllib.request.urlopen must NOT be the outbound mechanism (the exact
    # Cloudflare-blocked regression this pin forbids).  A stdlib http.server for
    # the *inbound* listener is fine; urllib.request.urlopen for the *outbound*
    # hop is not.
    assert "urlopen" not in src, (
        "the openrouter-shim outbound hop must NOT use urllib.request.urlopen — "
        "bare urllib is Cloudflare-BLOCKED (403) against openrouter.ai even with "
        "a correct credential; use curl (the proven standard fingerprint)"
    )

    # The Cloudflare rationale must be documented so a future editor does not
    # "simplify" back to urllib (matching the anthropic shim) and reintroduce the
    # 403.
    assert "cloudflare" in src.lower(), (
        "the openrouter-shim must DOCUMENT the Cloudflare finding (why not "
        "urllib) in its docstring, so the client choice is not silently reverted"
    )


def test_openrouter_shim_forwards_authorization_unchanged_no_reshaping():
    """The openrouter shim does NO header reshaping: its request-drop set must
    NOT strip `authorization` — contrast the anthropic-oauth-shim, which DOES."""
    mod = _load_openrouter_shim_module()

    drop = getattr(mod, "_DROP_REQ", None)
    assert drop is not None, (
        "the openrouter-shim must define a request-header drop set (_DROP_REQ) "
        "for hop-by-hop headers, like the anthropic-oauth-shim"
    )
    drop_lower = {h.lower() for h in drop}
    assert "authorization" not in drop_lower, (
        "the openrouter-shim must forward the incoming 'Authorization: Bearer' "
        "header UNCHANGED (no header reshaping) — it must NOT drop 'authorization' "
        f"the way the anthropic-oauth-shim does; _DROP_REQ={sorted(drop_lower)!r}"
    )
    assert "x-api-key" not in drop_lower, (
        "the openrouter-shim reshapes NOTHING: it must not carry the "
        "anthropic-oauth-shim's x-api-key strip; _DROP_REQ={!r}".format(
            sorted(drop_lower)
        )
    )
    # It DOES still drop the genuinely hop-by-hop headers a correct proxy must not
    # forward (host / content-length / accept-encoding), like the reference shim.
    for hop in ("host", "content-length", "accept-encoding"):
        assert hop in drop_lower, (
            f"the openrouter-shim must still drop the hop-by-hop header {hop!r} "
            f"(a correct reverse proxy does); _DROP_REQ={sorted(drop_lower)!r}"
        )

    # CONTRAST (fidelity anchor): the reference anthropic-oauth-shim DOES reshape
    # — it strips authorization AND x-api-key — so the "unlike the
    # anthropic-oauth-shim" clause of the scenario is bound to the REAL reference
    # shim, not a re-derivation.
    anthropic = _committed_oauth_shim_path()
    assert anthropic is not None, "the reference anthropic-oauth-shim must exist"
    a_src = anthropic.read_text()
    assert re.search(r'"x-api-key"', a_src) and re.search(
        r'"authorization"', a_src
    ), (
        "the reference anthropic-oauth-shim must reshape headers (strip "
        "x-api-key + authorization) — the contrast the openrouter-shim is 'unlike'"
    )


def test_openrouter_shim_is_stdlib_only_and_help_exits_zero():
    shim = _committed_openrouter_shim_path()
    assert shim is not None, "the committed openrouter-shim asset does not exist yet"
    src = shim.read_text()

    imported = _top_level_imported_modules(src)
    third_party = imported - set(sys.stdlib_module_names)
    assert not third_party, (
        "the openrouter-shim must import ONLY the python standard library (curl "
        "is an external binary invoked via subprocess, not a python import); "
        f"third-party imports found: {sorted(third_party)!r}"
    )

    proc = subprocess.run(
        [sys.executable, str(shim), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        "the committed openrouter-shim must expose an argparse --help that exits "
        f"zero (a broken shim fails the bake self-check); exit {proc.returncode}, "
        f"stderr={proc.stderr!r}"
    )
    # It accepts the same serve args the launcher passes (--host / --port /
    # --upstream), mirroring the anthropic-oauth-shim's CLI surface.
    for flag in ("--host", "--port", "--upstream"):
        assert flag in proc.stdout, (
            f"the openrouter-shim --help must document the {flag!r} serve arg "
            f"the launcher passes; help:\n{proc.stdout}"
        )


# ---------------------------------------------------------------------------
# Dockerfile bake
# ---------------------------------------------------------------------------


def _dockerfile_text() -> str:
    df = _find_bc_base_dockerfile()
    assert df is not None, "no bc-base Dockerfile found under the repo tree"
    return df.read_text()


def test_dockerfile_bakes_openrouter_shim_copy_and_chmod():
    text = _dockerfile_text()

    # COPY the committed openrouter-shim onto PATH at /usr/local/bin/openrouter-shim
    # (mirrors the anthropic-oauth-shim COPY line exactly).
    assert re.search(
        r"(?im)^\s*COPY\s+openrouter-shim\s+/usr/local/bin/openrouter-shim\b",
        text,
    ), (
        "the bc-base Dockerfile must COPY the committed openrouter-shim to "
        "/usr/local/bin/openrouter-shim (like the anthropic-oauth-shim COPY)"
    )
    # chmod it executable (0755) — like the anthropic-oauth-shim RUN chmod.
    assert re.search(
        r"(?im)chmod\s+0?755\s+/usr/local/bin/openrouter-shim\b", text
    ), (
        "the bc-base Dockerfile must chmod /usr/local/bin/openrouter-shim "
        "executable (0755), mirroring the anthropic-oauth-shim bake"
    )


def test_dockerfile_openrouter_shim_bake_leaves_launcher_self_pin_intact():
    """The added openrouter-shim COPY must NOT disturb the launcher self-pin
    literal / SHOPSYSTEM_BC_LAUNCHER_VERSION ARG lockstep (lead-tzw4y / lead-klxi):
    the literal VCS self-pin and the ARG default must still both equal the
    package's own release version."""
    import tomllib

    text = _dockerfile_text()
    pyproject = Path(_REPO_ROOT) / "pyproject.toml"
    version = tomllib.loads(pyproject.read_text())["project"]["version"]
    want = f"v{version}"

    pin = re.search(
        r"shopsystem-bc-launcher @ git\+https://github\.com/dstengle/"
        r"shopsystem-bc-launcher(?:\.git)?@(v\d+\.\d+\.\d+)",
        text,
    )
    assert pin is not None and pin.group(1) == want, (
        "the launcher VCS self-pin literal must still equal the package release "
        f"version {want!r}; got {pin.group(1) if pin else None!r} — the "
        "openrouter-shim COPY addition must not disturb it"
    )
    arg = re.search(
        r"(?im)^\s*ARG\s+SHOPSYSTEM_BC_LAUNCHER_VERSION=(v\d+\.\d+\.\d+)", text
    )
    assert arg is not None and arg.group(1) == want, (
        "the SHOPSYSTEM_BC_LAUNCHER_VERSION ARG default must still equal the "
        f"package release version {want!r}; got {arg.group(1) if arg else None!r}"
    )
