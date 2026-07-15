"""Fabro anthropic-oauth-shim start argv + script.

Extracted from the former bc_launcher/fabro.py (bead -7pa4 follow-up: fabro
package split). Re-exported via bc_launcher.fabro (the package __init__).
"""
from __future__ import annotations

from bc_launcher.constants import AGENT_VAULT_CONTAINER_CA_PATH, SSL_CERT_FILE_ENV
from bc_launcher.fabro.constants import *  # noqa: F401,F403  (sibling constants)




def _fabro_shim_start_argv() -> list[str]:
    """The argv the launcher uses to START the baked so2h shim in serve mode.

    These are the shim's REAL serve args (``--host`` / ``--port`` per the
    committed shim's argparse) targeting the fixed 127.0.0.1:8788 endpoint
    that fabro's anthropic base_url points at.  Returned as a list so the
    test can assert the launcher starts the shim at that mode + host + port.
    """
    return [
        ANTHROPIC_OAUTH_SHIM_BIN,
        "--host",
        FABRO_SHIM_HOST,
        "--port",
        str(FABRO_SHIM_PORT),
    ]



def _fabro_shim_start_script() -> str:
    """Build the ``/bin/sh -c`` script that starts the baked so2h shim as a
    BACKGROUND listener on 127.0.0.1:8788.

    The shim's ``serve_forever`` blocks, so it is backgrounded (``&``) and
    disowned via ``nohup`` so the exec returns and the listener survives.
    The argv is the shim's REAL serve args (``--host 127.0.0.1 --port 8788``).
    """
    import shlex

    argv = " ".join(shlex.quote(tok) for tok in _fabro_shim_start_argv())
    # nohup + background so the exec returns while the shim keeps listening;
    # its own stderr log line ("[shim] listening on ...") goes to a logfile
    # under the def dir so a launch never blocks on the serving loop.
    log = shlex.quote(f"{FABRO_DEF_CONTAINER_DIR}/anthropic-oauth-shim.log")
    # lead-ze4w BUG#3: the shim runs in a NON-LOGIN /bin/sh, so it never
    # sources /etc/profile.d/agent-vault-ca.sh -> SSL_CERT_FILE is empty and
    # the shim's urllib does not trust the agent-vault MITM CA (upstream HTTPS
    # via HTTPS_PROXY fails CERTIFICATE_VERIFY_FAILED).  Export SSL_CERT_FILE
    # explicitly to the materialized broker CA path (parallel to the clone
    # path's GIT_SSL_CAINFO export) so the shim trusts the MITM CA.
    ssl_export = (
        f"export {SSL_CERT_FILE_ENV}="
        f"{shlex.quote(AGENT_VAULT_CONTAINER_CA_PATH)}"
    )
    return f"{ssl_export}\nnohup {argv} >{log} 2>&1 &\n"



def _openrouter_shim_start_argv() -> list[str]:
    """The argv the launcher uses to START the baked openrouter-shim in serve mode
    (lead-ifye3.5 behavior 1).

    Mirrors the anthropic-oauth-shim's ``--host`` / ``--port`` serve args, but
    targets the openrouter-shim's OWN distinct loopback port
    (127.0.0.1:FABRO_OPENROUTER_SHIM_PORT) — the endpoint fabro's openrouter-
    override ``[llm.providers.openai]`` base_url points at.  Returned as a list so
    the test can assert the launcher starts the shim at that mode + host + port.
    """
    return [
        OPENROUTER_SHIM_BIN,
        "--host",
        FABRO_SHIM_HOST,
        "--port",
        str(FABRO_OPENROUTER_SHIM_PORT),
    ]



def _openrouter_shim_start_script() -> str:
    """Build the ``/bin/sh -c`` script that starts the baked openrouter-shim as a
    BACKGROUND listener on 127.0.0.1:FABRO_OPENROUTER_SHIM_PORT (lead-ifye3.5
    behavior 1).

    The SAME launch-lifecycle shape ``_fabro_shim_start_script`` uses for the
    anthropic-oauth-shim: the shim's ``serve_forever`` blocks, so it is
    backgrounded (``&``) and disowned via ``nohup`` so the exec returns and the
    unsandboxed, container-level listener survives.  SSL_CERT_FILE is pinned to
    the materialized broker CA path (same lead-ze4w BUG#3 non-login-shell reason
    the anthropic shim pins it) so the shim's own outbound OpenRouter call over
    HTTPS_PROXY trusts the agent-vault MITM CA.
    """
    import shlex

    argv = " ".join(shlex.quote(tok) for tok in _openrouter_shim_start_argv())
    log = shlex.quote(f"{FABRO_DEF_CONTAINER_DIR}/openrouter-shim.log")
    ssl_export = (
        f"export {SSL_CERT_FILE_ENV}="
        f"{shlex.quote(AGENT_VAULT_CONTAINER_CA_PATH)}"
    )
    return f"{ssl_export}\nnohup {argv} >{log} 2>&1 &\n"
