"""Agent-vault credential broker: CA materialization + proxy-URL builders.

Extracted verbatim from ``controller`` (Phase 1 of the controller.py
decomposition). Leaf module; re-exported by ``controller`` for import-path
compatibility. Do not import ``controller`` from here (would cycle).
"""
from __future__ import annotations

from urllib.parse import quote, urlparse

from bc_launcher.constants import AGENT_VAULT_CONTAINER_CA_PATH


# ---------------------------------------------------------------------------
# Agent-vault credential broker model (ADR-026, lead-hxb8 / lead-v4ih)
# ---------------------------------------------------------------------------
#
# ADR-026 (accepted 2026-06-09) locks the credential disposition: ZERO
# host-filesystem credential coupling reaches a BC container, for BOTH Claude
# OAuth and GitHub.  The agent-vault broker is the SOLE credential path; there
# is no launch-mode flag and no host-mount fallback.
#
# The launcher therefore:
#   * mounts NO host ~/.claude, ~/.config/gh, or ~/.gitconfig into the
#     container, and never consults BCLAUNCHER_HOST_HOME to resolve a
#     credential mount source;
#   * mounts a placeholder-only, read-only .credentials.json whose accessToken
#     is the literal AGENT_VAULT_PLACEHOLDER_TOKEN — never a real OAuth token;
#   * wraps the agent invocation as `agent-vault run -- claude`, with
#     HTTPS_PROXY pointed at the broker's proxy listener on the shopsystem
#     network, so the broker substitutes the real Claude OAuth and GitHub
#     credentials on outbound requests (the container never holds them);
#   * gates launch on an agent_vault_reachable readiness barrier alongside the
#     messaging-database barrier: the agent engages only when BOTH are
#     reachable.

# The literal accessToken value baked into the container's placeholder
# .credentials.json.  Scenarios 3931e43e / e4348b11 pin this exact string.
AGENT_VAULT_PLACEHOLDER_TOKEN = "__PLACEHOLDER__"


# Container path at which the placeholder Claude credential file lives and the
# proxy env var the agent-vault run wrapper honours for outbound substitution.
AGENT_VAULT_PROXY_ENV = "HTTPS_PROXY"


# Default agent-vault broker proxy listener address on the shopsystem network.
# The broker is provisioned out of band (scenario 2a4e9889); the launcher only
# needs its proxy-listener address to point HTTPS_PROXY at it and to probe
# readiness.  Overridable per-launch via the launch() ``agent_vault_broker``
# argument or the BCLAUNCHER_AGENT_VAULT_BROKER env var.
DEFAULT_AGENT_VAULT_BROKER = "http://agent-vault:14321"

AGENT_VAULT_BROKER_ENV = "BCLAUNCHER_AGENT_VAULT_BROKER"


# The agent-vault broker's control-API port — the port the readiness PROBE
# targets (a reachability check, NOT the :14322 MITM proxy the runtime traffic
# uses).
AGENT_VAULT_CONTROL_API_PORT = 14321

# The unqualified agent-vault service name on the (single-product) shopsystem
# network.  For a SECOND product the broker is reachable under a
# slug-qualified name (``<slug>-agent-vault``); see resolve_probe_broker_address.
AGENT_VAULT_SERVICE_NAME = "agent-vault"


# ---------------------------------------------------------------------------
# Agent-vault MITM proxy port + clone-time trust env (bclaunch-5fji)
# ---------------------------------------------------------------------------
#
# DEFECT 1 (bclaunch-5fji): the agent-vault *control API* listens on :14321
# (the DEFAULT_AGENT_VAULT_BROKER above and AGENT_VAULT_ADDR carry it).  But
# the credential-substituting MITM HTTPS proxy listens on a SEPARATE port,
# :14322, and requires basic-auth whose userinfo is the agent token + vault
# (``http://<token>:<vault>@<host>:14322`` — the same shape ``agent-vault run``
# itself uses).  Pointing a clone's HTTPS_PROXY at the bare control-API address
# reaches a listener that does not proxy, so the brokered clone fails.  The
# clone-time proxy URL must therefore be built against the MITM port with the
# agent credentials as userinfo.  The agent token already carries its
# operator-supplied agent-token prefix (see the AGENT_VAULT_TOKEN scenarios),
# so it is used verbatim as the userinfo username — NOT re-prefixed.
AGENT_VAULT_MITM_PROXY_PORT = 14322


# DEFECT 2 (bclaunch-5fji): the launch-time clone runs in a NON-LOGIN shell, so
# it never sources /etc/profile.d/agent-vault-ca.sh and therefore lacks
# GIT_SSL_CAINFO, producing 'unable to get local issuer certificate' when git
# verifies the broker's MITM cert.  The container CA path the bc-base entrypoint
# materializes the broker CA to is FIXED by the operator design (bclaunch-9rr);
# the controller sets GIT_SSL_CAINFO to that same path explicitly on the clone
# exec so the brokered clone trusts the broker CA without a login shell.
GIT_SSL_CAINFO_ENV = "GIT_SSL_CAINFO"


# ---------------------------------------------------------------------------
# Clone-prep CA materialization — write-path == trust-path (lead-z0v2)
# ---------------------------------------------------------------------------
#
# REGRESSION (lead-z0v2, empirical v0.3.34): the launcher LOGGED that it had
# "materialized the broker MITM root CA into the container trust store before
# the clone" yet the clone FAILED with
#   error setting certificate file: /home/vscode/.config/agent-vault/ca.pem
# because the CA file did NOT exist:
#   * the entrypoint materializer (agent-vault-ca.sh) writes the CA file ONLY
#     when AGENT_VAULT_CA_PEM is set + non-empty; in the real flagless launch
#     that env was EMPTY, so NOTHING was written; and
#   * the controller UNCONDITIONALLY set GIT_SSL_CAINFO to the CA path on the
#     clone exec, pointing git at a CA path that was never written.
# That is a write-path-vs-trust-path MISMATCH: git was pointed at a path the
# launcher never guaranteed to contain real CA content.
#
# FIX: a single clone-prep script that (a) ACTUALLY writes the broker MITM CA
# *content* to AGENT_VAULT_CONTAINER_CA_PATH BEFORE the clone — sourcing the
# content from AGENT_VAULT_CA_PEM when present (ADR-045 inline PEM), else from
# the working operator path `agent-vault ca fetch` — and (b) configures git to
# trust THAT SAME existing path.  The script EXITS NON-ZERO if the file does
# not end up non-empty with a "-----BEGIN CERTIFICATE-----" first line, so the
# launcher NEVER points git at an unwritten / empty / malformed CA path.  The
# write-path and the trust-path are the identical constant
# AGENT_VAULT_CONTAINER_CA_PATH, eliminating the mismatch.
CA_PEM_FIRST_LINE = "-----BEGIN CERTIFICATE-----"



def _clone_ca_materialize_script(ca_path: str = AGENT_VAULT_CONTAINER_CA_PATH) -> str:
    """Shell that writes real broker CA *content* to ``ca_path`` then verifies
    it, before the clone (lead-z0v2).

    Source precedence for the CA content:
      1. the inline ``AGENT_VAULT_CA_PEM`` env (ADR-045) when set + non-empty;
      2. else the working operator path ``agent-vault ca fetch`` (the same
         workaround an operator runs by hand: ``agent-vault ca fetch > <ca>``).

    The script then VERIFIES the file is a non-empty PEM whose first line is
    ``-----BEGIN CERTIFICATE-----``; if not, it exits non-zero so the caller
    refuses to point git at an unwritten / empty / malformed CA path.  This is
    the write-path side of the write==trust invariant; the caller sets
    GIT_SSL_CAINFO to this SAME ``ca_path`` only after this script succeeds.
    """
    return (
        "set -e; "
        f'ca="{ca_path}"; '
        'mkdir -p "$(dirname "$ca")"; '
        # (1) inline PEM (ADR-045) wins; (2) else operator `agent-vault ca fetch`.
        'if [ -n "${AGENT_VAULT_CA_PEM:-}" ]; then '
        '  printf \'%s\\n\' "$AGENT_VAULT_CA_PEM" > "$ca"; '
        "else "
        '  agent-vault ca fetch > "$ca"; '
        "fi; "
        # vscode runs the clone and must read the CA.
        'chown vscode:vscode "$ca" 2>/dev/null || true; '
        # VERIFY: non-empty AND first line is the PEM BEGIN marker.  Exit
        # non-zero otherwise so git is never pointed at a bad CA path.
        '[ -s "$ca" ] || { echo "agent-vault CA file is empty: $ca" >&2; exit 1; }; '
        # F3 grep-validation fix (lead-eqao): the BEGIN-CERTIFICATE marker
        # begins with "-----", so a bare `grep -qx "{marker}"` made grep parse
        # the dash-prefixed pattern as OPTIONS ("grep: unrecognized option") and
        # exit non-zero on an ACTUALLY-VALID cert, falsely "missing BEGIN
        # CERTIFICATE" and refusing the clone.  Use `-F` (fixed string, so no
        # regex interpretation) and `--` (end-of-options, so the dash-prefixed
        # marker is treated as a PATTERN, never as flags).  The negative limb
        # stays honest: genuinely marker-less content still fails to match `-x`
        # (whole-line) and is rejected fail-loud below.
        f'head -n 1 "$ca" | grep -qxF -- "{CA_PEM_FIRST_LINE}" '
        '|| { echo "agent-vault CA file missing BEGIN CERTIFICATE: $ca" >&2; exit 1; }'
    )



# ---------------------------------------------------------------------------
# Operator-supplied agent-vault credential + TLS-trust injection
# (bclaunch-5hi / bclaunch-7pf)
# ---------------------------------------------------------------------------
#
# The in-container `agent-vault run` client authenticates to the broker using
# an addr + token + vault triple.  These are OPERATOR-SUPPLIED at launch (from
# the CLI --env-file / process env / launch() params) and are injected into
# the container env under these names.  The TOKEN value is NEVER a literal
# baked into source — the only credential literal permitted in src/ is the
# AGENT_VAULT_PLACEHOLDER_TOKEN above.
AGENT_VAULT_ADDR_ENV = "AGENT_VAULT_ADDR"

AGENT_VAULT_TOKEN_ENV = "AGENT_VAULT_TOKEN"

AGENT_VAULT_VAULT_ENV = "AGENT_VAULT_VAULT"


# The broker CA travels as an env var (bclaunch-7pf REVISED, operator design
# directive — supersedes the 9ca2e05 CA bind-mount).  The CA is a PUBLIC
# ~574-byte cert (NOT secret).  A controller-side CA bind-mount is UNSAFE under
# nested-docker / host-path mismatch and the design goal is to eliminate
# controller bind mounts entirely.  So the operator supplies the CA PEM via
# --env-file as AGENT_VAULT_CA_PEM; the controller injects it into the
# container env, and the bc-base entrypoint (bclaunch-9rr) materializes it to a
# file and exports the TLS-trust vars.  The controller does NO CA handling and
# builds NO CA bind-mount.
AGENT_VAULT_CA_PEM_ENV = "AGENT_VAULT_CA_PEM"


# The fixed container path at which the bc-base entrypoint materializes the CA
# from AGENT_VAULT_CA_PEM.  Retained here only so tests/scenarios can name the
# (former) controller-side path and assert the controller builds NO mount at
# it; the controller itself no longer references it.
CONTAINER_BROKER_CA_PATH = "/etc/agent-vault/broker-ca.pem"


# The container-internal path of the placeholder Claude credentials file.
CONTAINER_CLAUDE_CREDENTIALS_PATH = "/home/vscode/.claude/.credentials.json"



def _mitm_proxy_host(agent_vault_addr: str) -> str | None:
    """Derive the broker's MITM-proxy host:port from AGENT_VAULT_ADDR.

    bclaunch-5fji DEFECT 1: ``AGENT_VAULT_ADDR`` names the broker's *control
    API* (e.g. ``https://agent-vault:14321`` — it carries a scheme and the
    control-API port).  The credential-substituting MITM HTTPS proxy lives on
    the SAME host but a DIFFERENT port (:14322).  This extracts the host from
    the addr (stripping any scheme and the control-API port) and returns
    ``<host>:14322``.  Returns ``None`` when no host can be derived (so the
    caller skips building a proxy URL).
    """
    if not agent_vault_addr:
        return None
    from urllib.parse import urlparse

    addr = agent_vault_addr.strip()
    # urlparse needs a scheme to populate ``hostname``; add a throwaway one
    # when the operator supplied a bare host:port.
    parsed = urlparse(addr if "://" in addr else "tcp://" + addr)
    host = parsed.hostname
    if not host:
        return None
    return f"{host}:{AGENT_VAULT_MITM_PROXY_PORT}"



def _build_clone_proxy_url(
    agent_vault_addr: str | None,
    agent_vault_token: str | None,
    agent_vault_vault: str | None,
) -> str | None:
    """Build the brokered-clone HTTPS_PROXY URL (bclaunch-5fji DEFECT 1).

    Shape (matching what ``agent-vault run`` uses):
        http://<token>:<vault>@<host>:14322

    * ``<host>`` is derived from ``agent_vault_addr`` with the MITM port
      (:14322) substituted for the control-API port.
    * userinfo is ``<token>:<vault>``, URL-encoded.  The agent token already
      carries its operator-supplied agent-token prefix (per the
      AGENT_VAULT_TOKEN scenarios), so it is used verbatim — NOT re-prefixed.

    Returns ``None`` when addr / token / vault are not all available (no
    operator credentials supplied), so the caller leaves the clone proxy unset
    rather than constructing a half-formed URL.
    """
    if not (agent_vault_addr and agent_vault_token and agent_vault_vault):
        return None
    host = _mitm_proxy_host(agent_vault_addr)
    if host is None:
        return None
    from urllib.parse import quote

    # ``safe=""`` so any ``@`` / ``:`` / ``/`` inside the token or vault is
    # percent-encoded and cannot corrupt the userinfo / authority boundary.
    user = quote(agent_vault_token, safe="")
    secret = quote(agent_vault_vault, safe="")
    return f"http://{user}:{secret}@{host}"



def _build_runtime_proxy_url(
    agent_vault_broker: str | None,
    agent_vault_addr: str | None,
    agent_vault_token: str | None,
    agent_vault_vault: str | None,
) -> str | None:
    """Build the CONTAINER's runtime HTTPS_PROXY value (bclaunch-3q12).

    bclaunch-3q12 DEFECT: ``controller.launch`` previously set the container's
    persistent ``HTTPS_PROXY`` to ``broker_address``, which DEFAULTS to
    ``DEFAULT_AGENT_VAULT_BROKER`` (``http://agent-vault:14321`` — the agent-vault
    *control API*) whenever the operator did not hand-build an explicit
    ``--agent-vault-broker`` URL.  At runtime claude-the-agent then inherited a
    proxy pointed at the control API (:14321), not the credential-substituting
    MITM HTTPS proxy (:14322), and its brokered Anthropic calls failed
    (``CONNECT tunnel failed`` / 405).

    The runtime proxy is the same shape the brokered CLONE already derives
    (``_build_clone_proxy_url``): ``http://<token>:<vault>@<host>:14322``.  This
    completes the runtime half of the lead-5fji DEFECT 1 fix so a plain
    ``bc-container launch`` with NO hand-built ``--agent-vault-broker`` URL sets
    the runtime proxy at the MITM listener automatically.

    Precedence (decided cleanly, bclaunch-3q12):

      1. An explicit ``agent_vault_broker`` (operator passed ``--agent-vault-broker``
         a full URL, or set ``BCLAUNCHER_AGENT_VAULT_BROKER``) WINS — it is used
         verbatim.  This preserves the pre-existing operator workaround/override:
         an operator may still pass any explicit proxy URL.
      2. Otherwise the proxy is DERIVED (:14322 + ``<token>:<vault>`` userinfo,
         the operator's agent-token used verbatim) from the env-file
         ``AGENT_VAULT_ADDR`` / ``AGENT_VAULT_TOKEN`` / ``AGENT_VAULT_VAULT``
         triple — the default for a plain brokered launch.

    Returns ``None`` only when neither path yields a value (no explicit broker
    AND the addr/token/vault triple is incomplete), so the caller can fall back
    to the control-API default address for readiness probing without claiming a
    derived runtime proxy that does not exist.
    """
    # (1) Explicit operator-supplied broker URL wins verbatim.
    if agent_vault_broker:
        return agent_vault_broker
    # (2) Else derive the :14322 MITM proxy from the env-file triple — the same
    #     derivation the clone exec already uses.
    return _build_clone_proxy_url(
        agent_vault_addr, agent_vault_token, agent_vault_vault
    )
