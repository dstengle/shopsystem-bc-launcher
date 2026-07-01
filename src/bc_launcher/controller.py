"""
Business logic for bc-container subcommands.

All Docker interaction goes through the DockerDriver interface, making this
layer fully testable without a live Docker daemon.
"""
from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from bc_launcher.driver import (
    ContainerMount,
    DockerDriver,
    DockerSocketUnreachableError,
    RegistryDriver,
)


# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

CONTAINER_WORKSPACE = "/workspace"
# Host path of the docker socket, bind-mounted into the container ONLY when the
# opt-in lead-only docker-socket flag is enabled (lead-zxtk,
# @scenario_hash:ff370a4e7e9dac5e / e177655ba09a73fa).
DOCKER_SOCKET_PATH = "/var/run/docker.sock"
AGENT_TMUX_SESSION = "agent"
# The container user that owns the agent tmux session and all of its
# clients (send-keys, capture-pane, has-session, attach-session).  The
# BC image's default USER is root; Claude Code refuses
# --dangerously-skip-permissions when EUID==0 for security reasons, so
# the agent must run as a non-root user.  vscode is the unprivileged
# user already provisioned in the BC base image with HOME=/home/vscode
# (the same home into which credential mounts and cp steps land).
AGENT_CONTAINER_USER = "vscode"
BC_IMAGE = "ghcr.io/dstengle/shopsystem-bc-base:latest"
BC_IMAGE_ENV = "BC_IMAGE"
SHOPMSG_DSN_ENV = "SHOPMSG_DSN"

# ---------------------------------------------------------------------------
# Launch-failure diagnostic file (lead-63em — re-issue of lead-2qta)
# ---------------------------------------------------------------------------
#
# When a launch fails to bring up a USABLE agent session, the operator needs
# to learn WHY from the HOST, without attaching into any tmux session and
# without relying on the launch command's stderr (ephemeral) or the
# bc-container monitor tmux pane (needs a live session that never came up).
# The launcher therefore writes a PERSISTED diagnostic FILE on the same
# host-visible per-BC surface the mailbox is read from.
#
# DOCUMENTED per-BC host-discoverable location (lead-63em RESOLUTION of the
# lead-2qta surface-ambiguity clarify):
#
#   <BCLAUNCHER_HOST_STATE_DIR>/<container-name>/launch-diagnostic.txt
#
# where the per-BC state root is the launcher host directory the operator's
# per-BC mailbox/state is read from.  It is resolved from the
# ``BCLAUNCHER_HOST_STATE_DIR`` env var when set, else defaults to a
# per-USER state directory (``$XDG_STATE_HOME/bc-launcher``, falling back to
# ``~/.local/state/bc-launcher``).  Each BC owns a per-BC subdirectory named
# for its container (``bc-<bc_name>``), exactly the per-BC layout shape the
# launcher already uses for the container identity surface, so the
# diagnostic file lands on the SAME per-BC surface and is host-discoverable
# at a single, documented, predictable path.  The launcher creates the
# directory tree on demand, so the surface exists even on the very first
# failed launch (when no container directory had been created yet).
#
# lead-bnhn (P1 bugfix): the default state root was ``/var/lib/bc-launcher``,
# which is root-owned and NOT writable by the invoking (shop-shell) user, so
# the on-demand ``mkdir(parents=True)`` in the diagnostic write raised
# PermissionError and ABORTED the very launch the diagnostic was supposed to
# describe.  The default is now a per-USER state directory the invoking user
# can always write to, so the documented host-discoverable surface no longer
# REQUIRES a root-pre-created path.  (The diagnostic write is ALSO wrapped
# best-effort / non-fatal at the controller call site — see
# ``_write_launch_diagnostic`` — so even an unwritable override location can
# never abort the launch.)
#
# The file is a single human-readable line carrying the literal cause-marker
# token (so an operator / tool can grep for the cause) followed by a
# human-readable reason describing why the session failed to come up.
BCLAUNCHER_HOST_STATE_DIR_ENV = "BCLAUNCHER_HOST_STATE_DIR"
XDG_STATE_HOME_ENV = "XDG_STATE_HOME"
# Per-USER default state-dir LEAF, joined under $XDG_STATE_HOME (or
# ~/.local/state when XDG_STATE_HOME is unset) — a location writable by the
# invoking user, never the root-owned /var/lib (lead-bnhn).
DEFAULT_HOST_STATE_DIR_LEAF = "bc-launcher"
LAUNCH_DIAGNOSTIC_FILENAME = "launch-diagnostic.txt"


def default_host_state_dir() -> Path:
    """The per-USER default launch-diagnostic state root (lead-bnhn).

    Resolves to ``$XDG_STATE_HOME/bc-launcher`` when ``XDG_STATE_HOME`` is set,
    else ``~/.local/state/bc-launcher`` (the XDG Base Directory default for
    per-user state).  This is a location the INVOKING (shop-shell) user can
    write to, so the on-demand parent ``mkdir`` in the diagnostic write does
    NOT require a root-pre-created ``/var/lib/bc-launcher`` and cannot raise
    PermissionError on a fresh-adopter bootstrap.  Used ONLY when
    ``BCLAUNCHER_HOST_STATE_DIR`` is unset; an explicit override still wins.
    """
    xdg = os.environ.get(XDG_STATE_HOME_ENV)
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".local" / "state"
    return base / DEFAULT_HOST_STATE_DIR_LEAF

# The four documented launch-failure cause-marker tokens.  Each is the
# literal token written into the diagnostic file's ``cause:`` field so the
# operator is pointed at the right repair.
CAUSE_MARKER_MESSAGING_DB = "messaging-db"
CAUSE_MARKER_AGENT_VAULT = "agent-vault"
CAUSE_MARKER_READINESS = "readiness"
CAUSE_MARKER_AGENT_STARTUP = "agent-startup"


def launch_diagnostic_path(bc_name: str) -> Path:
    """Documented per-BC host-discoverable launch-diagnostic file path.

    lead-63em.  Returns the absolute host path at which a failed launch's
    persisted diagnostic file lives for ``bc_name``:

        <state-root>/<container-name>/launch-diagnostic.txt

    The state root is ``BCLAUNCHER_HOST_STATE_DIR`` when set, else the
    per-USER ``default_host_state_dir()`` (``$XDG_STATE_HOME/bc-launcher``,
    default ``~/.local/state/bc-launcher`` — lead-bnhn: writable by the
    invoking user, never the root-owned ``/var/lib/bc-launcher``).  The
    per-BC subdirectory is the container name (``bc-<bc_name>``), matching
    the launcher's existing per-BC identity shape.  This is the SAME
    host-visible per-BC surface the operator's per-BC mailbox/state is read
    from — readable from the host with NO tmux attach and independent of the
    launch command's stderr.
    """
    override = os.environ.get(BCLAUNCHER_HOST_STATE_DIR_ENV)
    root = Path(override) if override else default_host_state_dir()
    return root / _container_name(bc_name) / LAUNCH_DIAGNOSTIC_FILENAME

# SHOPMSG_SYSTEM_SLUG (lead-53y0): bc-launcher RESOLVES + INJECTS this slug
# into the launched BC container's docker run env.  bc-launcher itself NEVER
# reads/consumes SHOPMSG_SYSTEM_SLUG — the CONSUMER is the BC's own shop-msg
# at runtime (messaging, lead-tgsb).  Resolution precedence for the injected
# value: SHOPMSG_SYSTEM_SLUG env on the launcher invocation > manifest
# product: > DEFAULT_SYSTEM_SLUG ('shopsystem').
SHOPMSG_SYSTEM_SLUG_ENV = "SHOPMSG_SYSTEM_SLUG"
DEFAULT_SYSTEM_SLUG = "shopsystem"

# lead-5k8c — the GitHub org that owns each BC's `<bc>-beads` Dolt remote
# (mirrors the BC_IMAGE org "ghcr.io/dstengle/...").  The per-BC beads remote
# is `git+https://github.com/<org>/<bc>-beads.git`; the agent-vault proxy
# injects credentials for it via HTTPS_PROXY at exec time.
BEADS_REMOTE_ORG = "dstengle"


def _beads_dolt_remote_url(bc_name: str) -> str:
    """The `git+https://` Dolt remote URL for a BC's `<bc>-beads` registry.

    lead-5k8c.  A BC named ``shopsystem-bc-launcher`` keeps its beads working
    set on the GitHub repo ``<org>/shopsystem-bc-launcher-beads.git``; the
    launcher's empty-remote provisioning seeds + pushes to this URL.
    """
    return (
        f"git+https://github.com/{BEADS_REMOTE_ORG}/{bc_name}-beads.git"
    )


def _is_empty_remote_failure(message: str) -> bool:
    """Whether a `bd bootstrap` failure was caused by an EMPTY Dolt remote.

    lead-5k8c.  An uninitialized `<bc>-beads` GitHub repo makes bootstrap's
    clone fail with "git remote has no branches: cannot push ...; initialize
    the repository with an initial branch/commit first".  That specific
    condition — and ONLY that condition — is what the empty-remote
    init-and-push provisioning recovers; other bootstrap failures fall
    straight through to the warn-and-continue path.
    """
    text = message.lower()
    return "git remote has no branches" in text or (
        "no branches" in text and "initialize" in text
    )


def _empty_remote_seed_script(beads_remote_url: str) -> str:
    """Shell to INITIALIZE an empty `<bc>-beads` Dolt remote (lead-5k8c).

    Mirrors the heal performed live 2026-06-22: `git init -b main` a temp
    repo seeded from the git-tracked `.beads/issues.jsonl`, push an initial
    commit to the `<bc>-beads.git` GitHub repo (agent-vault proxy injects
    creds via HTTPS_PROXY), then `bd dolt remote add origin <url>` +
    `bd dolt push`, and verify `refs/dolt/data` appears in `git ls-remote`.
    """
    return (
        f"set -e; cd {CONTAINER_WORKSPACE}; "
        # Materialize the committed registry into a throwaway init tree so the
        # seed commit carries the BC's tracked issues.
        "tmp=$(mktemp -d); "
        "cp .beads/issues.jsonl \"$tmp/issues.jsonl\" 2>/dev/null || true; "
        "git -C \"$tmp\" init -b main >/dev/null; "
        "git -C \"$tmp\" add -A; "
        "git -C \"$tmp\" -c user.email=bc-launcher@shopsystem "
        "-c user.name=bc-launcher commit -m 'seed beads remote' >/dev/null; "
        f"git -C \"$tmp\" push \"{beads_remote_url}\" main >/dev/null; "
        # Point the local bd working set at the now-initialized remote and push
        # the embedded-Dolt working set up.
        f"bd dolt remote add origin {beads_remote_url} || true; "
        "bd dolt push || true; "
        # Verify the remote now carries Dolt data refs (init succeeded).
        f"git ls-remote {beads_remote_url} 'refs/dolt/*' | grep -q refs/dolt"
    )

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
AGENT_VAULT_CONTAINER_CA_PATH = "/home/vscode/.config/agent-vault/ca.pem"
GIT_SSL_CAINFO_ENV = "GIT_SSL_CAINFO"

# lead-ze4w BUG#3: the fabro shim + `fabro engage` are started via NON-LOGIN
# `/bin/sh -c`, so they never source the login profile
# /etc/profile.d/agent-vault-ca.sh that exports SSL_CERT_FILE -> SSL_CERT_FILE
# is EMPTY -> the shim's urllib does not trust the agent-vault MITM CA ->
# upstream HTTPS via HTTPS_PROXY fails CERTIFICATE_VERIFY_FAILED (502).  EXACT
# parallel to the clone-path GIT_SSL_CAINFO export (DEFECT 2 above): the
# controller sets SSL_CERT_FILE explicitly on the shim + engage exec env,
# pointing python/urllib at the SAME materialized broker CA path the clone
# path trusts, so upstream TLS verifies without a login shell.
SSL_CERT_FILE_ENV = "SSL_CERT_FILE"


def _fabro_exec_env() -> dict[str, str]:
    """The extra exec env the launcher pins on the fabro shim + engage execs.

    lead-ze4w BUG#3: these execs run in a non-login `/bin/sh -c` that never
    sources /etc/profile.d/agent-vault-ca.sh, so SSL_CERT_FILE (the python /
    urllib CA-trust var) is empty and the shim's upstream HTTPS through
    HTTPS_PROXY fails CERTIFICATE_VERIFY_FAILED.  Set SSL_CERT_FILE explicitly
    to the SAME materialized broker CA path the clone path points git at via
    GIT_SSL_CAINFO, so the shim + engage trust the agent-vault MITM CA without
    a login shell.
    """
    return {SSL_CERT_FILE_ENV: AGENT_VAULT_CONTAINER_CA_PATH}

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
# Self-contained fabro loop def bundle (lead-h2bj — S2 def-bundle delivery)
# ---------------------------------------------------------------------------
#
# The launcher ships the bc-shop Implementer->Reviewer loop fabro def (ADR-051)
# as a set of packaged ASSET files under ``src/bc_launcher/assets/fabro-def/``
# and PLACES them into every launched container so the container carries a
# self-contained fabro def runnable FROM THE DEF ALONE — nothing is fetched at
# run time.  The def-root layout (``workflow.fabro`` / ``workflow.toml`` /
# ``project.toml`` / ``vaults/default/secrets.json`` / ``nodes/*.md``) is
# reproduced verbatim inside the container at ``/workspace/.fabro/`` (the
# project.toml comment pins that in a real ``.fabro/`` tree this file lives at
# ``.fabro/project.toml``).
#
# NATIVE-VAULT INVARIANT (ADR-049): the def's fabro vault
# (``vaults/default/secrets.json``) ships ``__PLACEHOLDER__``-only; real
# credentials ride the agent-vault surface (the shim + HTTPS_PROXY baked in
# S1), NEVER the fabro vault.  The asset file is placed verbatim, so no real
# secret is introduced by placement.
#
# Placement mechanism: the DockerDriver exposes only ``exec_run`` (docker exec)
# — there is no ``docker cp`` seam — so the bundle is placed by a single
# ``/bin/sh -c`` script that base64-decodes each file's bytes into its
# def-root-relative path.  base64 keeps the file bytes EXACTLY intact through
# the shell (no quoting/escaping/newline hazards regardless of file content),
# so the placed def is byte-identical to the shipped asset.
FABRO_DEF_CONTAINER_DIR = f"{CONTAINER_WORKSPACE}/.fabro"
FABRO_DEF_ASSET_SUBDIR = "assets/fabro-def"
# The 15 def-root-relative paths the bundle ships (acceptance criterion 0).
# Enumerated explicitly so a dropped/renamed asset is a loud failure, not a
# silently-thinner bundle.
FABRO_DEF_FILES: tuple[str, ...] = (
    "workflow.fabro",
    "workflow.toml",
    "project.toml",
    "vaults/default/secrets.json",
    "nodes/bc-implementer.md",
    "nodes/bc-review.md",
    "nodes/bc-reviewer.md",
    "nodes/bc-router.md",
    "nodes/bc-sufficiency-check.md",
    "nodes/integrating-to-main.md",
    "nodes/subagent-driven-development.md",
    "nodes/test-driven-development.md",
    "nodes/using-git-worktrees.md",
    "nodes/work-done-gate.md",
    "nodes/writing-plans-bdd.md",
)


def _fabro_def_asset_root() -> Path:
    """Absolute path to the packaged fabro-def asset directory.

    Resolves relative to THIS module so the bundle is found whether the
    launcher runs from a source checkout or an installed wheel (the assets are
    packaged as package data under ``bc_launcher/assets/``).
    """
    return Path(__file__).resolve().parent / FABRO_DEF_ASSET_SUBDIR


def _load_fabro_def_files() -> dict[str, bytes]:
    """Read the 15 packaged def-bundle asset files as raw bytes.

    Returns a mapping of def-root-relative path -> file bytes.  Raises
    ``FileNotFoundError`` if any enumerated asset is missing, so a broken
    package surfaces loudly rather than placing a thinner bundle.
    """
    root = _fabro_def_asset_root()
    out: dict[str, bytes] = {}
    for rel in FABRO_DEF_FILES:
        src = root / rel
        out[rel] = src.read_bytes()
    return out


def _fabro_def_install_script(
    files: dict[str, bytes],
    dest_dir: str = FABRO_DEF_CONTAINER_DIR,
) -> str:
    """Build a ``/bin/sh -c`` script that places the def bundle into a container.

    lead-h2bj.  Each file's bytes are base64-encoded on the HOST and decoded on
    the CONTAINER into ``<dest_dir>/<relpath>``, so the placed def is
    byte-identical to the shipped asset regardless of file content (no shell
    quoting/escaping/newline hazards).  The script creates each file's parent
    directory first so the ``nodes/`` and ``vaults/default/`` subtrees are
    reproduced exactly.
    """
    import base64
    import shlex

    lines = ["set -e", f"mkdir -p {shlex.quote(dest_dir)}"]
    for rel in FABRO_DEF_FILES:
        data = files[rel]
        b64 = base64.b64encode(data).decode("ascii")
        target = f"{dest_dir}/{rel}"
        parent = os.path.dirname(target)
        q_target = shlex.quote(target)
        q_parent = shlex.quote(parent)
        lines.append(f"mkdir -p {q_parent}")
        # base64 -d is POSIX-portable on the bc-base image (coreutils).
        lines.append(f"printf %s {shlex.quote(b64)} | base64 -d > {q_target}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Fabro orchestrator launch path — anthropic-oauth-shim + fabro provider wiring
# (lead-vwib — LAUNCHER WIRING ONLY; the shim itself is lead-so2h's owned
# artifact, a REAL stdlib ThreadingHTTPServer reverse proxy baked into bc-base
# v0.3.44 at /usr/local/bin/anthropic-oauth-shim)
# ---------------------------------------------------------------------------
#
# The DEFAULT launch path is the ADR-050 tmux/engage-tier path and is
# UNCHANGED by this wiring.  The FABRO orchestrator launch path is an
# ADDITIVE mode (``launch_path="fabro"`` / ``--fabro-path``) that, during
# bring-up, additionally:
#
#   (a) STARTS the baked so2h shim in-container as a background listener with
#       the shim's REAL serve args: ``anthropic-oauth-shim --host 127.0.0.1
#       --port 8788``.  Once listening, an in-container agent's Anthropic
#       traffic (its dummy ``x-api-key``) has a local endpoint; the shim
#       strips ``x-api-key``, adds ``Authorization: Bearer <dummy>`` +
#       ``anthropic-beta: oauth-2025-04-20``, and forwards via HTTPS_PROXY so
#       agent-vault injects the real credential on the wire.
#
#   (b) WRITES fabro's EFFECTIVE settings into the placed def at
#       ``/workspace/.fabro/settings.toml`` with ``[llm.providers.anthropic]``
#       ``base_url = "http://127.0.0.1:8788/v1"`` and ``adapter = "anthropic"``
#       (ADR-049 D2 — native Anthropic Messages format in both directions, NO
#       OpenAI<->Anthropic translation adapter).
#
# NATIVE-VAULT INVARIANT (ADR-049 D1): the fabro vault stays
# ``__PLACEHOLDER__``-only and NO real credential is written into the fabro
# settings or the shim's own configuration on this path.  The real Anthropic
# credential rides ONLY the agent-vault surface on the wire via the container
# HTTPS_PROXY (the live dummy-x-api-key -> shim -> HTTPS_PROXY -> agent-vault
# -> real-OAuth-200 round-trip is the lead's E2E, fabro-orchestration/02
# @scenario_hash:9c7b4e8280665239 — NOT this launch path's in-container core).
LAUNCH_PATH_TMUX = "tmux"
LAUNCH_PATH_FABRO = "fabro"

# The baked so2h shim binary + its REAL serve args (must match the shim's
# own argparse: --host / --port).  Binding on 127.0.0.1:8788 is the endpoint
# fabro's anthropic base_url points at.
ANTHROPIC_OAUTH_SHIM_BIN = "/usr/local/bin/anthropic-oauth-shim"
FABRO_SHIM_HOST = "127.0.0.1"
FABRO_SHIM_PORT = 8788

# The placed def's effective fabro settings file (settings.toml lives at the
# def root alongside workflow.fabro / project.toml).
FABRO_SETTINGS_CONTAINER_PATH = f"{FABRO_DEF_CONTAINER_DIR}/settings.toml"

# The placed def's workflow.toml (run/environment config for workflow.fabro).
# lead-ze4w BUG#2: the packaged asset carries byte-verbatim
# BC_NAME=fabro-throwaway / WORK_ID=fabro-spike-demo-3 in BOTH [run.inputs]
# (agent prompts) AND [run.environment.env] (the native script= sandbox
# overlay).  The native script= nodes (arm/armed/worktree/integ/wdg_r/emit_r)
# read $BC_NAME / $WORK_ID from the [run.environment.env] overlay, and `fabro
# run -I` overrides ONLY [run.inputs] (agent prompts), NOT the native command
# sandbox env — so the placed workflow.toml's overlay must be REWRITTEN to the
# launch's ACTUAL bc_name / work_id, or every native node runs against the
# bundle-default identity.  The launcher rewrites the placed workflow.toml the
# SAME way it (re)writes settings.toml: it generates the corrected file bytes
# on the host and base64-decode-writes them over the placed path.
FABRO_WORKFLOW_TOML_CONTAINER_PATH = f"{FABRO_DEF_CONTAINER_DIR}/workflow.toml"
# The bundle-default identity values the packaged workflow.toml ships (the
# values the rewrite must REPLACE).  Named so a test can assert the placed
# file no longer carries them.
FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME = "fabro-throwaway"
FABRO_WORKFLOW_TOML_DEFAULT_WORK_ID = "fabro-spike-demo-3"

# The [llm.providers.anthropic] surface the launcher writes into fabro's
# effective settings on the fabro path.  base_url points the built-in
# anthropic provider at the local shim; adapter stays "anthropic" (native
# format, no translation — ADR-049 D2).  NO credential slot is written here:
# the credential rides agent-vault, never the fabro settings (ADR-049 D1).
FABRO_ANTHROPIC_BASE_URL = f"http://{FABRO_SHIM_HOST}:{FABRO_SHIM_PORT}/v1"
FABRO_ANTHROPIC_ADAPTER = "anthropic"

# Fabro orchestrator ENGAGE step (lead-cadr — S4).  On the fabro launch path,
# AFTER the readiness barrier passes, the launcher REPLACES the tmux/claude
# engage tier (ADR-050 D3) with two execs:
#
#   1. Start an EPHEMERAL, in-container fabro server running provider=local in
#      the FOREGROUND with NO web UI, bound to a local 127.0.0.1 socket, via
#      the argv `fabro server start --foreground --no-web` — the loop runs
#      headless inside the ONE bc-base container; nothing is orchestrated
#      outside it.
#   2. Run the placed ADR-051 Implementer->Reviewer loop def against that
#      server as the engage: `fabro run workflow.fabro -I BC_NAME=<bc> -I
#      WORK_ID=<work_id>`, carrying BC_NAME + WORK_ID into the run via the
#      def's [run.environment.env].
#
# The engage tier is REPLACED, not added alongside: on the fabro path the
# launcher starts NO tmux `agent` send-keys session and NO `claude` engage
# (reproduces fabro-orchestration/01 @scenario_hash:1aeace4c593ab14f via the
# real bc-container launch path).  Container / credential-proxy / postgres DSN
# / shop-msg mailbox surfaces are UNCHANGED from the tmux path (ADR-050 D1/D2
# launch parity) — only the engage tier differs.
FABRO_BIN = "fabro"
# The placed def's workflow file (relative to the def dir the engage runs in).
FABRO_WORKFLOW_FILE = "workflow.fabro"


# lead-ze4w BUG#4: `fabro server start` reads a SERVER-level config at
# ~/.fabro/settings.toml.  The launcher previously wrote ONLY the workflow-level
# /workspace/.fabro/settings.toml, never the server-level file, so the server
# aborted `server.auth.methods: field is required` and `fabro run` could not
# reach it.  Before starting the server, `_fabro_engage` bootstraps the
# ephemeral server config:
#   1. `fabro install --non-interactive --skip-llm --overwrite-settings
#      --github-strategy token --github-username <dummy>` (with GH_TOKEN set)
#      writes a valid server-level ~/.fabro/settings.toml (auth.methods etc.).
#   2. register the built-in anthropic provider pointed at the shim base_url
#      (http://127.0.0.1:8788/v1) with a DUMMY ANTHROPIC_API_KEY in the server
#      env — the real credential rides agent-vault on the wire (ADR-049 D1),
#      NEVER this config, so the key is a fixed placeholder literal.
#
# lead-8q2x Defect A (INSTALL-FLAG DRIFT): on fabro 0.254.0 `fabro install
# --non-interactive --skip-llm --overwrite-settings` ABORTS with
# `x non-interactive install requires --github-strategy`.  The empirically-
# verified minimal recipe that resolves the flag chain (and still writes
# [server.auth] methods=["dev-token"] + session secret + dev token) adds
# `--github-strategy token --github-username <any>` with `GH_TOKEN=<any>` in
# the env.  fabro's github token is NOT exercised on this path — the
# anthropic-oauth-shim + agent-vault carry the real creds — so GH_TOKEN and
# --github-username are DUMMY placeholders (ADR-049 D1: no real cred literal).
FABRO_SERVER_INSTALL_GITHUB_USERNAME = "fabro-throwaway"
FABRO_SERVER_INSTALL_GH_TOKEN = "gh-dummy-agent-vault-rides-the-wire"
FABRO_SERVER_INSTALL_ARGV: tuple[str, ...] = (
    FABRO_BIN,
    "install",
    "--non-interactive",
    "--skip-llm",
    "--overwrite-settings",
    "--github-strategy",
    "token",
    "--github-username",
    FABRO_SERVER_INSTALL_GITHUB_USERNAME,
)
# The dummy ANTHROPIC_API_KEY placed in the ephemeral server env so the
# registered anthropic provider is well-formed.  ADR-049 D1: NO real
# credential — the real cred rides agent-vault on the wire.  This is the only
# credential-shaped literal on this path and is deliberately a placeholder.
FABRO_SERVER_DUMMY_ANTHROPIC_KEY = "sk-ant-dummy-agent-vault-rides-the-wire"
# lead-8q2x Defect C (PROVIDER NOT REGISTERED AT SERVER): `--skip-llm` skips
# server-level provider registration, so even with the server up
# `fabro model test --model haiku` reports "not configured" — only the
# workflow-level /workspace/.fabro/settings.toml carries
# [llm.providers.anthropic], which the SERVER does NOT read for fabro
# model/run resolution.  So the engage bootstrap must register the anthropic
# provider AT THE SERVER by appending [llm.providers.anthropic] (base_url
# pointed at the shim + a DUMMY key) to the server-level ~/.fabro/settings.toml
# AFTER `fabro install`.  The container user is `vscode` (HOME=/home/vscode),
# so the server-level config lives at /home/vscode/.fabro/settings.toml.
FABRO_SERVER_SETTINGS_CONTAINER_PATH = (
    f"/home/{AGENT_CONTAINER_USER}/.fabro/settings.toml"
)


def _fabro_server_start_argv() -> list[str]:
    """The argv the launcher uses to START the ephemeral in-container fabro
    server on the fabro engage path (lead-cadr).

    provider=local, FOREGROUND, NO web UI, bound to a local 127.0.0.1 socket:
    the loop runs headless inside the one bc-base container.  Returned as a
    list so the test can assert the launcher issues exactly this argv.
    """
    return [FABRO_BIN, "server", "start", "--foreground", "--no-web"]


def _fabro_server_install_argv() -> list[str]:
    """The argv the launcher uses to BOOTSTRAP the ephemeral server config
    before `fabro server start` (lead-ze4w BUG#4).

    `fabro install --non-interactive --skip-llm --overwrite-settings` writes a
    valid server-level ~/.fabro/settings.toml (carrying the required
    server.auth.methods surface), so `fabro server start` does not abort
    `server.auth.methods: field is required`.
    """
    return list(FABRO_SERVER_INSTALL_ARGV)


def _fabro_run_argv(bc_name: str, work_id: str) -> list[str]:
    """The argv the launcher uses to RUN the placed loop def against the
    ephemeral fabro server as the ENGAGE step (lead-cadr).

    `fabro run workflow.fabro -I BC_NAME=<bc_name> -I WORK_ID=<work_id>` — the
    ADR-051 Implementer->Reviewer loop def (scenario 75) is the agent loop that
    engages; BC_NAME + WORK_ID ride into the run via the def's
    [run.environment.env].  Returned as a list so the test can assert the
    launcher issues exactly this argv with the scenario's BC_NAME / WORK_ID.
    """
    return [
        FABRO_BIN,
        "run",
        FABRO_WORKFLOW_FILE,
        "-I",
        f"BC_NAME={bc_name}",
        "-I",
        f"WORK_ID={work_id}",
    ]


def _fabro_engage_script(bc_name: str, work_id: str) -> str:
    """Build the ``/bin/sh -c`` script that drives the fabro ENGAGE step in the
    placed def dir (lead-cadr + lead-ze4w BUG#4).

    lead-ze4w BUG#4: BEFORE `fabro server start`, bootstrap the SERVER-level
    fabro config so the server does not abort
    ``server.auth.methods: field is required``:
      1. `fabro install --non-interactive --skip-llm --overwrite-settings
         --github-strategy token --github-username <dummy>` (GH_TOKEN set)
         writes a valid server-level ~/.fabro/settings.toml.
      2. register the anthropic provider pointed at the shim base_url
         (http://127.0.0.1:8788/v1) with a DUMMY ANTHROPIC_API_KEY in the
         server env (ADR-049 D1: no real credential — the real cred rides
         agent-vault on the wire).

    lead-8q2x — the ze4w Fix#4 bootstrap was broken 3 ways at runtime; all
    three are corrected here:

      A) INSTALL-FLAG DRIFT: on fabro 0.254.0 the bare install aborts
         ``x non-interactive install requires --github-strategy``.
         `_fabro_server_install_argv` now emits
         `--github-strategy token --github-username <dummy>` and the install
         runs with a DUMMY `GH_TOKEN` inline (github token not exercised on
         this path).
      B) `&` CWD-SCOPING: previously the trailing `&` backgrounded the WHOLE
         `cd && install && ... && nohup server` AND-list, so the `cd` ran
         inside the backgrounded subshell and the PARENT shell cwd stayed
         /workspace (the image WORKDIR) — `fabro run workflow.fabro` then
         resolved /workspace/workflow.fabro -> "x workflow not found".  Now
         the `cd {def_dir}` + install + provider registration run
         SYNCHRONOUSLY in the SAME shell as `fabro run`, and ONLY the
         foreground server is backgrounded (`nohup ... &` on its own line).
         `fabro run` therefore executes with cwd=/workspace/.fabro and
         resolves /workspace/.fabro/workflow.fabro.
      C) PROVIDER NOT REGISTERED AT SERVER: `--skip-llm` skips server-level
         provider registration, so `fabro model test` reported "not
         configured" even with the server up — the SERVER does not read the
         workflow-level settings for model resolution.  The bootstrap now
         APPENDS a `[llm.providers.anthropic]` block (shim base_url + DUMMY
         key) to the server-level ~/.fabro/settings.toml AFTER install, so
         the provider is registered AT THE SERVER.

    Then start the ephemeral fabro server in the FOREGROUND with no web UI and
    run the loop def against it.  The server's foreground serve loop blocks, so
    ONLY the server is backgrounded; the ``fabro run`` engage runs
    synchronously in the same shell (cwd=/workspace/.fabro) so
    ``workflow.fabro`` resolves and the server picks up the effective settings
    the launcher wrote alongside it.
    """
    import shlex

    install_argv = " ".join(
        shlex.quote(tok) for tok in _fabro_server_install_argv()
    )
    server_argv = " ".join(
        shlex.quote(tok) for tok in _fabro_server_start_argv()
    )
    run_argv = " ".join(
        shlex.quote(tok) for tok in _fabro_run_argv(bc_name, work_id)
    )
    def_dir = shlex.quote(FABRO_DEF_CONTAINER_DIR)
    server_log = shlex.quote(f"{FABRO_DEF_CONTAINER_DIR}/fabro-server.log")
    base_url = shlex.quote(FABRO_ANTHROPIC_BASE_URL)
    dummy_key = shlex.quote(FABRO_SERVER_DUMMY_ANTHROPIC_KEY)
    gh_token = shlex.quote(FABRO_SERVER_INSTALL_GH_TOKEN)
    server_settings = shlex.quote(FABRO_SERVER_SETTINGS_CONTAINER_PATH)
    # (Defect C) The [llm.providers.anthropic] block appended to the
    # server-level ~/.fabro/settings.toml so the provider is registered AT THE
    # SERVER pointed at the shim, with a DUMMY key (ADR-049 D1 — real cred
    # rides agent-vault on the wire).  Written with printf via a here-doc-free
    # append so it needs no base64 helper on the container.
    provider_block = (
        "\\n[llm.providers.anthropic]\\n"
        f'base_url = "{FABRO_ANTHROPIC_BASE_URL}"\\n'
        f'api_key = "{FABRO_SERVER_DUMMY_ANTHROPIC_KEY}"\\n'
    )
    provider_register = (
        f"printf '%b' {shlex.quote(provider_block)} >> {server_settings}"
    )
    # (Defect A/B/C) Bootstrap the server-level config SYNCHRONOUSLY, then
    # background ONLY the foreground server, then run the def in the SAME shell
    # (cwd=/workspace/.fabro):
    #   * `cd {def_dir}` first, SYNCHRONOUS — so `fabro run` resolves
    #     /workspace/.fabro/workflow.fabro (NOT /workspace/workflow.fabro);
    #   * `GH_TOKEN=<dummy> fabro install ... --github-strategy token
    #     --github-username <dummy>` writes a valid ~/.fabro/settings.toml and
    #     no longer aborts on the flag chain;
    #   * append [llm.providers.anthropic] (shim base_url + DUMMY key) to the
    #     SERVER-level settings so the provider is registered at the server;
    #   * export the agent-vault CA + shim base_url in the server env;
    #   * background ONLY `nohup {server} ... &` (its OWN line) so the run can
    #     engage against it while the parent shell stays in {def_dir}.
    # NOTE (Defect B): the server is backgrounded via a brace group
    # ``{ nohup ... & }`` so ONLY the server subprocess is detached; the
    # surrounding `&&` chain (cd, install, provider-register, exports) runs
    # SYNCHRONOUSLY in the CURRENT shell, so the cwd set by `cd {def_dir}`
    # persists to `fabro run` on the last line.  (If the whole `cd && ... &&
    # nohup server` list were terminated by a bare trailing `&`, the entire
    # list — including the `cd` — would run in a backgrounded subshell and the
    # parent shell cwd would stay at the image WORKDIR; that was the bug.)
    return (
        f"cd {def_dir} && "
        f"GH_TOKEN={gh_token} {install_argv} && "
        f"{provider_register} && "
        f'export {SSL_CERT_FILE_ENV}={shlex.quote(AGENT_VAULT_CONTAINER_CA_PATH)} && '
        f"export ANTHROPIC_API_KEY={dummy_key} && "
        f"export ANTHROPIC_BASE_URL={base_url} && "
        f"{{ nohup {server_argv} >{server_log} 2>&1 & }} && "
        f"{run_argv}\n"
    )


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


def _fabro_settings_toml() -> str:
    """The effective fabro settings TOML the launcher writes on the fabro path.

    Carries ``[llm.providers.anthropic]`` with ``base_url`` pointed at the
    local shim and ``adapter = "anthropic"`` (native format, no translation).
    Writes NO credential slot — the real Anthropic credential rides
    agent-vault on the wire, never fabro's settings (ADR-049 D1/D2).
    """
    return (
        "# settings.toml -- EFFECTIVE fabro settings written by the "
        "shopsystem-bc-launcher\n"
        "# fabro orchestrator launch path (lead-vwib).  Points fabro's "
        "built-in\n"
        "# anthropic provider at the in-container anthropic-oauth-shim "
        "(lead-so2h)\n"
        f"# listening on {FABRO_SHIM_HOST}:{FABRO_SHIM_PORT}.  The adapter "
        'stays "anthropic"\n'
        "# so the shim speaks native Anthropic Messages format in both "
        "directions;\n"
        "# NO OpenAI<->Anthropic translation adapter is introduced "
        "(ADR-049 D2).\n"
        "#\n"
        "# ADR-049 D1: NO real credential is written here.  The real "
        "Anthropic\n"
        "# credential rides ONLY the agent-vault surface on the wire via the\n"
        "# container HTTPS_PROXY; fabro's native vault stays "
        '"__PLACEHOLDER__"-only.\n'
        "\n"
        "[llm.providers.anthropic]\n"
        f'base_url = "{FABRO_ANTHROPIC_BASE_URL}"\n'
        f'adapter = "{FABRO_ANTHROPIC_ADAPTER}"\n'
    )


def _fabro_settings_install_script(
    dest_path: str = FABRO_SETTINGS_CONTAINER_PATH,
) -> str:
    """Build a ``/bin/sh -c`` script that writes the effective fabro settings
    into the placed def at ``dest_path``.

    The TOML bytes are base64-encoded on the HOST and decoded on the
    CONTAINER (same byte-safe channel the def-bundle placement uses), so the
    written settings are byte-identical to ``_fabro_settings_toml()``
    regardless of content.
    """
    import base64
    import shlex

    data = _fabro_settings_toml().encode("utf-8")
    b64 = base64.b64encode(data).decode("ascii")
    q_target = shlex.quote(dest_path)
    q_parent = shlex.quote(os.path.dirname(dest_path))
    return (
        "set -e\n"
        f"mkdir -p {q_parent}\n"
        f"printf %s {shlex.quote(b64)} | base64 -d > {q_target}\n"
    )


def _fabro_workflow_toml_rewrite(source: str, bc_name: str, work_id: str) -> str:
    """Rewrite the packaged workflow.toml's BC_NAME / WORK_ID to the launch's
    ACTUAL values (lead-ze4w BUG#2).

    The packaged asset ships BC_NAME / WORK_ID in TWO tables:
      * ``[run.inputs]``          — the agent-prompt inputs (`fabro run -I`
                                    overrides these, but only for prompts);
      * ``[run.environment.env]`` — the env overlay that reaches the native
                                    ``script=`` sandbox as real shell env vars
                                    ($BC_NAME / $WORK_ID), which `-I` does NOT
                                    override.

    Both carry the bundle defaults (``fabro-throwaway`` /
    ``fabro-spike-demo-3``).  This rewrites EVERY ``BC_NAME = "..."`` and
    ``WORK_ID = "..."`` assignment (in either table) to the launch's actual
    ``bc_name`` / ``work_id``, so the native nodes run against the real
    identity rather than the bundle default.  Modeled on the settings.toml
    (re)write: the corrected bytes are produced on the host and written over
    the placed file.
    """
    def _sub(line: str) -> str:
        # Match a top-of-line TOML key assignment `KEY = "value"` (optional
        # trailing comment preserved), for BC_NAME / WORK_ID only.
        m = re.match(
            r'^(?P<key>BC_NAME|WORK_ID)(?P<sp>\s*=\s*)"[^"]*"(?P<rest>.*)$',
            line,
        )
        if not m:
            return line
        value = bc_name if m.group("key") == "BC_NAME" else work_id
        # Escape any embedded double-quote / backslash so the emitted TOML
        # string stays well-formed regardless of the identity value.
        safe = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'{m.group("key")}{m.group("sp")}"{safe}"{m.group("rest")}'

    return "\n".join(_sub(line) for line in source.split("\n"))


def _fabro_workflow_toml_install_script(
    bc_name: str,
    work_id: str,
    dest_path: str = FABRO_WORKFLOW_TOML_CONTAINER_PATH,
) -> str:
    """Build a ``/bin/sh -c`` script that (re)writes the placed workflow.toml
    with the launch's ACTUAL BC_NAME / WORK_ID (lead-ze4w BUG#2).

    Reads the packaged workflow.toml asset, rewrites the BC_NAME / WORK_ID
    assignments in ``[run.inputs]`` and ``[run.environment.env]`` to the
    launch's values, then base64-decode-writes the corrected bytes over the
    placed ``workflow.toml`` — the SAME byte-safe channel + overwrite
    mechanism the launcher uses to (re)write settings.toml.
    """
    import base64
    import shlex

    asset = (_fabro_def_asset_root() / "workflow.toml").read_text(
        encoding="utf-8"
    )
    rewritten = _fabro_workflow_toml_rewrite(asset, bc_name, work_id)
    b64 = base64.b64encode(rewritten.encode("utf-8")).decode("ascii")
    q_target = shlex.quote(dest_path)
    q_parent = shlex.quote(os.path.dirname(dest_path))
    return (
        "set -e\n"
        f"mkdir -p {q_parent}\n"
        f"printf %s {shlex.quote(b64)} | base64 -d > {q_target}\n"
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

# Claude Code readiness markers used to sequence prompt injection inside
# the agent tmux session.  The default tmux session command is bash, so a
# naïve send-keys of the startup prompt lands in bash and fails as
# "-bash: <first-word>: command not found".  The launch sequence is:
#   1. send-keys 'claude --dangerously-skip-permissions' Enter
#                                         — start Claude Code with
#                                           in-container permission bypass
#                                           (the BC container is the
#                                           isolation boundary)
#   2. wait for CLAUDE_READY_MARKER       — workspace-trust banner appeared
#                                           (PRE-trust: this is the line
#                                           Claude Code prints BEFORE the
#                                           trust prompt clears, so it
#                                           confirms the agent has reached
#                                           interactive UI without
#                                           presupposing trust was accepted)
#   3. send-keys Enter                    — accept workspace-trust default
#                                           (empirically verified that
#                                           --dangerously-skip-permissions
#                                           does NOT bypass workspace trust,
#                                           so this step is still required)
#   4. wait for CLAUDE_INPUT_READY_MARKER — main input prompt is live
#                                           (POST-trust: "bypass permissions
#                                           on" appears only once the trust
#                                           prompt has cleared and the
#                                           main input UI is live — chosen
#                                           in preference to the bare "❯"
#                                           glyph because the PRE-trust
#                                           pane also contains "❯" as the
#                                           trust-prompt selector arrow,
#                                           which would otherwise cause
#                                           step 4 to succeed trivially)
#   5. send-keys <startup_prompt> Enter   — prompt lands inside Claude Code
# On any wait timeout, the launcher emits a stderr warning naming the
# step that did not confirm.
CLAUDE_READY_MARKER = "Accessing workspace:"
CLAUDE_INPUT_READY_MARKER = "bypass permissions on"
CLAUDE_READINESS_TIMEOUT_SECONDS = 60.0

# ---------------------------------------------------------------------------
# Blocking interactive option-screen handling on engage (lead-q3uy)
# ---------------------------------------------------------------------------
#
# After the input-ready marker is observed (step 4) but BEFORE the startup
# prompt is submitted (step 5), the in-container agent runtime can present a
# blocking interactive option screen (e.g. a "select an option" / settings /
# theme chooser) that absorbs keystrokes — so a naive prompt submission would
# be eaten by the screen instead of reaching the input prompt.  The launcher
# captures the pane at this point and recognizes a blocking option screen by
# the OPTION_SCREEN_MARKER signature.
#
# Disposition (lead-q3uy):
#   * If the captured screen ALSO carries an ESCAPE_AFFORDANCE_MARKER (the
#     screen advertises an Escape/dismiss key), send a DISCRETE tmux send-keys
#     carrying ONLY the Escape key (NEVER Enter) to dismiss it, CAPTURE the
#     rendered screen content, log it as a host-discoverable WARNING (the same
#     launch-stderr surface every other engage warning uses), then proceed to
#     submit the startup prompt directly — no host-side `bc-container inject`.
#   * If the screen exposes NO escape affordance, do NOT send Enter and do NOT
#     auto-confirm a default (pressing Enter would blindly select whatever
#     option is highlighted); instead surface a WARNING NAMING the un-escapable
#     screen so a human can review it from the host, and do NOT submit the
#     prompt into a screen that would swallow it.
#
# Detection keys on rendered-pane substrings, mirroring the existing
# CLAUDE_*_MARKER readiness-marker idiom rather than inventing a new seam.  The
# ESCAPE key NAME is the tmux key-name token sent as the SOLE send-keys payload
# (a discrete pty write that the TUI processes as a single Escape keypress).
OPTION_SCREEN_MARKER = "Select an option"
ESCAPE_AFFORDANCE_MARKER = "esc to"
ESCAPE_KEY_NAME = "Escape"

# ---------------------------------------------------------------------------
# Readiness-wait interactive-prompt auto-dismissal (lead-cw7m / lead-c713)
# ---------------------------------------------------------------------------
#
# EXTENDS the lead-q3uy/gs03 Esc-not-Enter / warn / no-auto-confirm posture
# from the ENGAGE phase (AFTER input-ready) to the READINESS-WAIT phase
# (BEFORE input-ready).  The new bc-base Claude Code image (c50b3b) renders
# an EARLIER interactive prompt — "Try the new fullscreen renderer?
# (1. Yes / 2. Not now, Esc to cancel)" — BEFORE the "Accessing workspace:"
# trust banner.  The narrow step-4 readiness handler (wait for
# CLAUDE_INPUT_READY_MARKER) could not see past it: the input-ready marker
# never appeared, the wait timed out at 60s, the startup prompt was never
# injected, the watcher never armed, and the BC never came online.
#
# Disposition (lead-cw7m — launcher-runtime scan-and-solve; the PO chose
# this over an image-config pre-seed because it is robust to image-config
# drift):
#   * During the readiness wait (while waiting for the input-ready marker),
#     if the pane presents an interactive prompt that is NOT the
#     already-handled workspace-trust prompt and is NOT yet at input-ready,
#     dismiss it with a safe NON-COMMITTAL default by sending ONLY Esc
#     (decline — NEVER Enter / '1', so the renderer is NOT enabled), emit a
#     host-discoverable WARNING NAMING the auto-dismissed prompt, then
#     CONTINUE the readiness loop toward input-ready.
#   * The whole scan-dismiss loop stays BOUNDED by the existing 60s readiness
#     timeout.  On timeout WITHOUT input-ready: STOP attempting dismissals
#     (no infinite loop), warn that the main input did not become ready
#     within 60 seconds, and proceed WITHOUT injecting the startup prompt.
#
# Detection keys on rendered-pane substrings, mirroring the CLAUDE_*_MARKER /
# OPTION_SCREEN_MARKER idiom.  A readiness-wait prompt is recognized as a
# blocking interactive prompt that advertises an Esc/cancel affordance and is
# NOT the workspace-trust prompt and is NOT yet at input-ready.  The specific
# fullscreen-renderer onboarding prompt is recognized by its own signature.
READINESS_PROMPT_ESCAPE_AFFORDANCE_MARKERS = ("esc to", "esc to cancel")
WORKSPACE_TRUST_PROMPT_MARKERS = ("trust this folder", "Quick safety check")
FULLSCREEN_RENDERER_PROMPT_MARKER = "Try the new fullscreen renderer?"
# How long a single input-ready wait poll is given before the controller
# re-captures the pane to look for a blocking readiness-wait prompt.  The
# per-attempt budget keeps the loop responsive while the TOTAL elapsed time
# stays bounded by CLAUDE_READINESS_TIMEOUT_SECONDS.
READINESS_DISMISS_POLL_SECONDS = 5.0


def _readiness_wait_blocking_prompt(pane: str) -> str | None:
    """Classify a readiness-wait pane capture (lead-cw7m / lead-c713).

    Returns a short human-readable NAME of a blocking interactive prompt that
    is presenting during the readiness wait and must be auto-dismissed with
    Esc, or ``None`` when the pane carries no such prompt.

    A prompt qualifies when ALL hold:
      * the input-ready marker is NOT yet present (an input-ready pane is not a
        blocking prompt — it is success);
      * the pane is NOT the already-handled workspace-trust prompt (step 3 of
        the readiness sequence accepts that one with Enter);
      * the pane advertises an Esc/cancel affordance (so Esc is the screen's
        own non-committal decline default — we never blind-press Enter / '1').

    The specific fullscreen-renderer onboarding prompt (image c50b3b) is named
    explicitly; any other Esc-dismissable readiness-wait prompt is named
    generically from its first non-empty rendered line.
    """
    if not pane:
        return None
    if CLAUDE_INPUT_READY_MARKER in pane:
        # Input-ready reached — not a blocking prompt.
        return None
    if any(m in pane for m in WORKSPACE_TRUST_PROMPT_MARKERS):
        # The workspace-trust prompt is handled by step 3 (Enter); do NOT
        # treat it as an unexpected prompt to Esc-dismiss.
        return None
    pane_lower = pane.lower()
    if not any(m in pane_lower for m in READINESS_PROMPT_ESCAPE_AFFORDANCE_MARKERS):
        # No Esc/cancel affordance advertised — not an Esc-dismissable prompt.
        return None
    if FULLSCREEN_RENDERER_PROMPT_MARKER in pane:
        return FULLSCREEN_RENDERER_PROMPT_MARKER
    # Generic readiness-wait prompt: name it by its first non-empty line.
    for line in pane.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "an unexpected interactive prompt"


def _container_name(bc_name: str) -> str:
    return f"bc-{bc_name}"


def beads_prefix_for(bc_name: str) -> str:
    """Derive a *fallback* beads issue_prefix from the BC name.

    A BC named ``shopsystem-<identifier>`` would, by name-derivation, carry a
    prefix derived from the identifier: lowercase, non-alphanumerics stripped,
    the ``shopsystem-`` namespace prefix dropped (e.g. ``shopsystem-messaging``
    → ``messaging``).

    NOTE — name-derivation is NOT authoritative (lead-rply).  A cloned repo's
    committed registry may carry a DIFFERENT prefix than the BC name implies
    (e.g. ``shopsystem-bc-launcher`` name-derives ``bclauncher`` but its
    committed registry uses ``bclaunch``; ``shopsystem-templates`` name-derives
    ``templates`` but uses ``tmpl``).  The launcher MUST adopt the COMMITTED
    prefix the cloned repo already carries — see
    ``_committed_beads_prefix`` — and only fall back to this name-derived value
    when the clone carries no committed registry from which a prefix can be
    read.
    """
    ident = bc_name
    if ident.startswith("shopsystem-"):
        ident = ident[len("shopsystem-"):]
    ident = re.sub(r"[^a-z0-9]", "", ident.lower())
    return ident


# Issue ids in a beads registry are ``<prefix>-<suffix>`` where the suffix is a
# short base36-ish token (e.g. ``bclaunch-eaa``).  The committed prefix is the
# segment before the FINAL hyphen of an issue id.
_BEADS_ISSUE_ID_RE = re.compile(r'"id"\s*:\s*"(?P<id>[^"]+)"')


def committed_beads_prefix_from_registry(registry_text: str) -> str | None:
    """Extract the committed issue_prefix from a ``.beads/issues.jsonl`` blob.

    The committed registry is JSONL: one issue object per line, each carrying an
    ``"id":"<prefix>-<suffix>"`` field.  The committed prefix is the portion of
    the first issue id up to (but excluding) its final hyphen.  Returns ``None``
    when the blob carries no parseable issue id (e.g. an empty registry), so the
    caller can fall back to name-derivation rather than configuring an empty
    prefix.
    """
    for match in _BEADS_ISSUE_ID_RE.finditer(registry_text or ""):
        issue_id = match.group("id")
        if "-" in issue_id:
            return issue_id.rsplit("-", 1)[0]
    return None


def _slugify(text: str) -> str:
    """Lowercase and replace runs of spaces with hyphens."""
    return re.sub(r"\s+", "-", text.strip().lower())


def _resolve_shop_network(start_dir: Path | None = None) -> str | None:
    """Resolve the shop's docker network name from on-disk shop configuration.

    This is the fallback network source (lead-ngzl): when no explicit
    ``--network`` is given AND bc-manifest.yaml carries no shop-level
    network/product field, the network is resolved from the shop's known
    on-disk configuration rather than hard-erroring.  Per ADR-038 D3 the
    precedence is: explicit override > manifest product > on-disk shop
    network / hard default ``"shopsystem"``.

    SEQUENCING (lead-ngzl / ADR-043 D2): the canonical single-source
    ops-coordinates artifact (``bin/ops-coordinates``) does not exist yet
    (lead-7wta).  In the interim this resolves the network name from, in
    order:

      1. ``compose.yaml`` ``networks:`` — the first network entry that
         declares an explicit ``name:`` (the live shop wiring:
         ``networks: shopsystem: {name: shopsystem}``);
      2. the product slug from ``.claude/shop/name.md`` with a trailing
         ``-product`` suffix stripped (``"shopsystem-product"`` ->
         ``"shopsystem"``).

    Returns the resolved network name, or ``None`` when no on-disk shop
    network configuration is discoverable (the error path is then the
    caller's responsibility when ``--network`` is also absent).
    """
    import yaml

    base = start_dir or Path.cwd()

    # (1) compose.yaml networks: <key>: {name: <network>}
    compose_path = base / "compose.yaml"
    if compose_path.exists():
        try:
            data = yaml.safe_load(compose_path.read_text())
        except yaml.YAMLError:
            data = None
        if isinstance(data, dict):
            networks = data.get("networks")
            if isinstance(networks, dict):
                for spec in networks.values():
                    if isinstance(spec, dict):
                        name = spec.get("name")
                        if isinstance(name, str) and name.strip():
                            return name.strip()

    # (2) product slug from .claude/shop/name.md minus a "-product" suffix
    name_md = base / ".claude" / "shop" / "name.md"
    if name_md.exists():
        raw = name_md.read_text().strip()
        if raw:
            token = raw.splitlines()[0].strip()
            if token.endswith("-product"):
                token = token[: -len("-product")]
            if token:
                return _slugify(token)

    return None


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


def resolve_probe_broker_address(
    explicit_broker: str | None,
    system_slug: str | None,
) -> str:
    """Resolve the agent-vault broker address used for the READINESS PROBE.

    lead-cs7k DEFECT (b): the readiness probe must target the broker by a host
    the LAUNCHED CONTAINER's network can resolve.  The pre-fix code probed the
    hardcoded ``DEFAULT_AGENT_VAULT_BROKER`` (``http://agent-vault:14321``),
    which only resolves on the single-product ``shopsystem`` network.  For a
    SECOND product the broker lives on the product network under a
    slug-qualified name (e.g. ``dummyco-agent-vault``), so the probe host must
    DERIVE from the resolved product slug.

    Crucially this PROBE address is DECOUPLED from the runtime ``HTTPS_PROXY``
    value (``_build_runtime_proxy_url``): pointing the probe at
    ``dummyco-agent-vault:14321`` (the control-API reachability target) must
    NOT clobber the ``http://<token>:<vault>@<host>:14322`` MITM proxy the
    launched agent uses verbatim.  This function therefore returns ONLY the
    probe address and is never fed into the runtime-proxy env.

    Precedence:
      1. An explicit operator-supplied broker URL wins verbatim (it already
         names the broker the operator wants probed).
      2. Else, when a product slug is known, the probe host is
         ``<slug>-agent-vault`` on the control-API port.
      3. Else (no slug) the unqualified default broker.
    """
    if explicit_broker:
        return explicit_broker
    if system_slug and system_slug != DEFAULT_SYSTEM_SLUG:
        host = f"{_slugify(system_slug)}-{AGENT_VAULT_SERVICE_NAME}"
        return f"http://{host}:{AGENT_VAULT_CONTROL_API_PORT}"
    return DEFAULT_AGENT_VAULT_BROKER


def _resolve_host_path(devcontainer_path: Path) -> Path:
    """
    If running inside a devcontainer where ``devcontainer_path`` lies on a bind
    mount, return the corresponding host-visible source path.  Falls back to
    ``devcontainer_path`` if no covering bind mount is found (i.e., not inside
    a bind-mounted devcontainer).

    Needed because mount sources passed to ``docker run`` are interpreted by
    the host docker daemon — bind-mount sources like ``/home/vscode/.claude``
    that are valid inside the launching container may not exist on the host.

    Resolution order:
      1. If ``BCLAUNCHER_HOST_HOME`` env var is set and the path is under the
         current ``Path.home()``, substitute the env var for the home prefix.
         This handles devcontainers whose home is bind-mounted from a host
         user home that we know explicitly.
      2. Otherwise walk ``/proc/self/mountinfo`` for the longest mount-point
         prefix that covers the path, and substitute the source root.
      3. Otherwise return the path unchanged.
    """
    try:
        target = str(devcontainer_path.resolve())
    except OSError:
        target = str(devcontainer_path)
    host_home = os.environ.get("BCLAUNCHER_HOST_HOME")
    if host_home:
        home_str = str(Path.home())
        if target == home_str:
            return Path(host_home)
        if target.startswith(home_str + "/"):
            return Path(host_home + target[len(home_str):])
    best_mount_point: str | None = None
    best_source_root: str | None = None
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 5:
                    continue
                source_root = parts[3]
                mount_point = parts[4]
                if target == mount_point or target.startswith(mount_point + "/"):
                    if best_mount_point is None or len(mount_point) > len(best_mount_point):
                        best_mount_point = mount_point
                        best_source_root = source_root
    except OSError:
        return devcontainer_path
    if best_mount_point is None or best_source_root is None:
        return devcontainer_path
    if target == best_mount_point:
        resolved = best_source_root
    else:
        suffix = target[len(best_mount_point):]
        resolved = best_source_root + suffix
    # mountinfo source roots may be dataset-relative (start with "/<user>/...")
    # rather than absolute host paths.  When BCLAUNCHER_HOST_HOME is set, apply
    # the same home-prefix substitution to the mountinfo result so it lands at
    # an absolute host path.
    if host_home:
        user_leaf = "/" + Path(host_home).name
        if resolved == user_leaf:
            return Path(host_home)
        if resolved.startswith(user_leaf + "/"):
            return Path(host_home + resolved[len(user_leaf):])
    return Path(resolved)


class ManifestProductTypeError(Exception):
    """Raised when a bc-manifest.yaml file's `product:` field is not a string.

    Carries enough structured context that the CLI can format a single-line
    error message naming the field, file path, expected type, and observed
    type — without exposing the underlying ``AttributeError`` that would
    otherwise surface from ``_slugify`` downstream.

    Per lead-393: the launch path must convert this into a non-zero exit
    with a clean stderr message; a Python traceback is only acceptable when
    the operator opts in via ``--debug`` (or ``BCLAUNCHER_DEBUG=1``).
    """

    def __init__(
        self,
        manifest_path: Path,
        observed_type: str,
        *,
        field: str = "product",
        expected_type: str = "string",
    ) -> None:
        self.manifest_path = manifest_path
        self.field = field
        self.expected_type = expected_type
        self.observed_type = observed_type
        super().__init__(self.format_message())

    def format_message(self) -> str:
        """Single-line stderr-ready message naming field, file, types."""
        return (
            f"bc-manifest.yaml: field {self.field!r} in {self.manifest_path} "
            f"has wrong type: expected {self.expected_type}, "
            f"got {self.observed_type}"
        )


def _resolve_manifest_remote(manifest_path: Path, bc_name: str) -> str | None:
    """Resolve the git remote URL registered for ``bc_name`` in bc-manifest.yaml.

    lead-uiwu FACET 1.  bc-manifest.yaml registers each BC with its remote URL
    and is "the declared source of remote URLs when launching BCs".  When a
    ``bc-container launch`` carries NO ``--repo-url`` and NO
    ``--workspace-mount``, the launcher resolves the BC's clone source from the
    manifest's ``bcs[].remote`` entry for the named BC and clones it into
    ``/workspace`` — rather than starting a container with a SILENT empty,
    non-git ``/workspace``.

    This is DISTINCT from ``_read_product_from_manifest`` (the manifest
    ``product:`` field, used for network/system-slug derivation): this reads the
    per-BC ``remote:`` field.  Returns the remote URL string for ``bc_name``, or
    ``None`` when the manifest is absent / unparseable / carries no resolvable
    remote for that BC, so the caller can apply the no-source loud-failure path
    (FACET 1 negative, scenario 0b50d090c9cc3c45).
    """
    import yaml
    from bc_launcher.manifest import load_manifest
    if not manifest_path.exists():
        return None
    try:
        manifest = load_manifest(manifest_path)
    except (yaml.YAMLError, ValueError):
        return None
    for entry in manifest.entries:
        if entry.name == bc_name:
            remote = (entry.remote or "").strip()
            return remote or None
    return None


def _read_product_from_manifest(manifest_path: Path) -> str | None:
    """Read the 'product' field from a bc-manifest.yaml file.

    Returns None if the file does not exist or has no 'product' key.
    Raises yaml.YAMLError on parse failure.
    Raises ManifestProductTypeError if 'product' is present but not a string.
    """
    import yaml
    if not manifest_path.exists():
        return None
    data = yaml.safe_load(manifest_path.read_text())
    if not isinstance(data, dict):
        return None
    if "product" not in data:
        return None
    product = data["product"]
    if product is None:
        # `product: null` (explicitly null) is just as broken as `product: 42`
        # for the downstream slugify call — name the field rather than silently
        # falling through to the "no network" branch, which would otherwise
        # hide the malformed-field root cause behind an unrelated error.
        raise ManifestProductTypeError(
            manifest_path=manifest_path,
            observed_type="null",
        )
    if not isinstance(product, str):
        raise ManifestProductTypeError(
            manifest_path=manifest_path,
            observed_type=type(product).__name__,
        )
    return product


# ---------------------------------------------------------------------------
# Result type returned by commands that produce output
# ---------------------------------------------------------------------------

@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str = ""


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------

class BcContainerController:
    """
    Pure-Python controller for bc-container operations.

    Accepts a DockerDriver at construction time so tests can inject fakes.
    """

    def __init__(
        self,
        driver: DockerDriver,
        registry_driver: RegistryDriver | None = None,
        monotonic=None,
    ) -> None:
        self._driver = driver
        # Injectable monotonic-clock seam (lead-cw7m).  The bounded
        # readiness-wait scan-dismiss loop budgets its TOTAL elapsed time
        # against this clock so the dismissal loop terminates at the 60s
        # readiness timeout rather than looping indefinitely.  Production
        # passes nothing (time.monotonic); tests inject a deterministic clock
        # to drive the bounded-timeout path without real wall-clock waits.
        self._monotonic = monotonic if monotonic is not None else time.monotonic
        # Optional registry seam (scenario af2f03d3ac519cb5).  When present,
        # launch resolves the bc-base "latest" tag's current registry digest
        # BEFORE starting the container, and runs the container from that
        # resolved digest rather than whatever digest the local cache holds
        # under "latest".  Absent (the default), launch runs from BC_IMAGE as
        # before — the resolution step is purely additive.
        self._registry_driver = registry_driver

    # ------------------------------------------------------------------
    # launch
    # ------------------------------------------------------------------

    def launch(
        self,
        bc_name: str,
        repo_url: str | None = None,
        shopmsg_dsn: str | None = None,
        image: str | None = None,
        startup_prompt: str | None = None,
        network: str | None = None,
        shop_network: str | None = None,
        manifest_path: Path | None = None,
        credential_home: Path | None = None,
        agent_vault_broker: str | None = None,
        agent_vault_addr: str | None = None,
        agent_vault_token: str | None = None,
        agent_vault_vault: str | None = None,
        workspace_mount: str | None = None,
        mount_docker_socket: bool = False,
        launch_path: str = LAUNCH_PATH_TMUX,
        work_id: str | None = None,
        debug: bool = False,
    ) -> CommandResult:
        """
        Start a Docker container for the named BC.

        Idempotent: if the container is already running, report and exit 0.

        Network resolution (in priority order — ADR-038 D3):
        1. If ``network`` is provided explicitly, use it as-is (no auto-create).
        2. Otherwise, read ``product:`` from bc-manifest.yaml (at ``manifest_path``
           or ``Path("bc-manifest.yaml")`` in CWD), slugify it, and use that as the
           network name.  If the network does not yet exist, create it first.
        3. Otherwise, fall back to ``shop_network`` — the shop's docker network
           name resolved from on-disk shop configuration (lead-ngzl).  When the
           manifest carries no shop-level network/product field, launch resolves
           the network from the shop's known on-disk config (the canonical
           network is ``shopsystem``, derived in the interim from the
           ``compose.yaml`` network / product slug — see
           ``_resolve_shop_network``) instead of hard-erroring.  If this network
           does not yet exist, create it first.
        4. Only when NEITHER an explicit ``network``, NOR a manifest product,
           NOR an on-disk ``shop_network`` is resolvable does launch return a
           non-zero "no network" error.  The error path is NARROWED (lead-ngzl):
           a manifest that merely lacks a product field no longer hard-errors so
           long as the shop network is resolvable on disk.

        Credential model (ADR-026 — agent-vault broker, the SOLE path; CA via
        env var per the operator no-bind-mount directive):
        NO host credential directory or file is bind-mounted into the
        container, and the controller builds ZERO credential/CA bind mounts.
        ``BCLAUNCHER_HOST_HOME`` is not consulted to resolve any credential
        mount source.  Instead:
          - the placeholder-only ``.credentials.json`` whose ``accessToken`` is
            the literal ``"__PLACEHOLDER__"`` is BAKED INTO the bc-base image at
            ``/home/vscode/.claude/.credentials.json`` (never a real OAuth
            token; no controller mount);
          - the operator-supplied broker CA PEM travels as the
            ``AGENT_VAULT_CA_PEM`` container env var (supplied via
            ``--env-file``); the bc-base entrypoint materializes it to a file
            and exports the TLS-trust vars.  The controller does NO CA handling;
          - the agent is invoked wrapped as ``agent-vault run -- claude`` with
            ``HTTPS_PROXY`` pointed at the broker's proxy listener on the
            shopsystem network, so the broker substitutes the real Claude OAuth
            and GitHub credentials on outbound requests — the container itself
            never holds them.
        Launch is gated on an ``agent_vault_reachable`` readiness barrier
        (alongside the messaging-database barrier): the agent is engaged only
        when BOTH the messaging database AND the agent-vault broker are
        reachable.  ``agent_vault_broker`` overrides the broker proxy address
        (falling back to ``BCLAUNCHER_AGENT_VAULT_BROKER`` then the default).
        ``credential_home`` is retained only for staging the placeholder file
        in tests; no host credential is read from it.
        """
        container = _container_name(bc_name)

        if self._driver.is_running(container):
            return CommandResult(
                exit_code=0,
                stdout=f"{container} is already running\n",
            )

        # --- Manifest product: (shared middle tier — lead-53y0) ---
        # Read the DECLARED manifest ``product:`` ONCE, up front, so all three
        # identity surfaces (docker network name, BC-name-shape prefix, and the
        # injected SHOPMSG_SYSTEM_SLUG) derive from the ONE resolver with the
        # manifest product as the shared middle tier.  Reading it here (rather
        # than only inside the network branch) means an explicit --network does
        # NOT suppress system-slug derivation from manifest product:.
        #
        # lead-393 reconciliation: a non-string ``product:`` (int/bool/null/
        # list/dict) is fatal ONLY when the manifest product is actually NEEDED
        # — i.e. when --network was NOT supplied and the network must derive
        # from it.  An explicit --network short-circuits the manifest typecheck
        # (the operator is not relying on the manifest), so a malformed product:
        # under an explicit --network is swallowed and the system slug falls
        # back to its default rather than blocking the launch.
        effective_manifest = manifest_path or Path("bc-manifest.yaml")
        manifest_product: str | None
        try:
            manifest_product = _read_product_from_manifest(effective_manifest)
        except ManifestProductTypeError as exc:
            if network is not None:
                # Explicit --network: do not block on a malformed manifest
                # product; treat it as absent for the slug surface.
                manifest_product = None
            elif debug:
                # In debug mode the operator opts back into the full traceback.
                raise
            else:
                # Network MUST derive from manifest product but it is malformed:
                # surface a clean single-line stderr message naming the field,
                # file path, expected type, observed type — NOT the
                # AttributeError that ``_slugify`` would raise downstream.
                return CommandResult(
                    exit_code=1,
                    stdout="",
                    stderr=exc.format_message() + "\n",
                )

        # --- Network resolution ---
        # The docker network name derives from the SAME product resolver
        # (lead-53y0 unification): there is no pre-existing per-surface env
        # override for the network, so the network product is
        #   manifest product: > default — slugified.
        resolved_network: str | None = network
        auto_create_network = False

        if resolved_network is None:
            if manifest_product:
                resolved_network = _slugify(manifest_product)
                auto_create_network = True
            elif shop_network:
                # On-disk shop network fallback (lead-ngzl, ADR-038 D3): when
                # the manifest carries no shop-level network/product field,
                # resolve the network from the shop's known on-disk config
                # rather than hard-erroring.  Same auto-create semantics as a
                # manifest-derived network.
                resolved_network = shop_network
                auto_create_network = True
            else:
                # NARROWED error path (lead-ngzl): fire ONLY when NEITHER an
                # on-disk shop network NOR --network is resolvable — NOT merely
                # because the manifest lacks a product field.
                return CommandResult(
                    exit_code=1,
                    stdout="",
                    stderr="no network: bc-manifest.yaml not found and --network not provided\n",
                )

        # Create the derived network if it does not yet exist (only for auto-derived, not explicit)
        if auto_create_network and not self._driver.network_exists(resolved_network):
            self._driver.network_create(resolved_network)

        # --- Repo-source resolution (lead-uiwu FACET 1) ---
        # The clone source is resolved with this precedence:
        #   1. an explicit ``--repo-url`` (``repo_url``) wins;
        #   2. an explicit ``--workspace-mount`` bind-mounts a host tree and
        #      SKIPS the clone entirely (handled in the mounts block below);
        #   3. otherwise — NO repo flags — RESOLVE the BC's git remote from
        #      bc-manifest.yaml (its per-BC ``remote:`` field; the manifest is
        #      "the declared source of remote URLs when launching BCs") and
        #      clone THAT into ``/workspace`` (scenario bdec2754d9135086).
        #
        # REGRESSION FIX: previously, a launch with neither ``--repo-url`` nor
        # ``--workspace-mount`` fell straight through to agent-start with an
        # EMPTY, non-git ``/workspace`` — no clone, no error (the silent empty
        # launch).  Now, when NO source is resolvable (no ``--repo-url``, no
        # ``--workspace-mount``, and no manifest remote for the BC), the launch
        # FAILS LOUDLY with a non-zero exit naming all three unresolvable
        # sources (scenario 0b50d090c9cc3c45) — it never silently succeeds with
        # an empty ``/workspace``.
        if repo_url is None and workspace_mount is None:
            resolved_remote = _resolve_manifest_remote(effective_manifest, bc_name)
            if resolved_remote:
                repo_url = resolved_remote
                out_lines_remote = (
                    f"Resolved repo remote for {bc_name!r} from "
                    f"{effective_manifest} (bc-manifest.yaml)\n"
                )
            else:
                return CommandResult(
                    exit_code=1,
                    stdout="",
                    stderr=(
                        f"no repo source for {bc_name!r}: could not resolve a "
                        f"clone source — neither --repo-url, --workspace-mount, "
                        f"nor a bc-manifest.yaml remote for {bc_name!r} was "
                        f"available; refusing to launch with an empty, non-git "
                        f"/workspace\n"
                    ),
                )
        else:
            out_lines_remote = None

        # --- Agent-vault broker resolution (ADR-026) ---
        # No host credential path is resolved; the broker is the sole
        # credential path.  Resolve the broker's CONTROL-API address from the
        # explicit arg, then the env override, then the default.  This address
        # is used for the readiness PROBE (agent_vault_reachable) below — the
        # control API on :14321 is the right target for a reachability check.
        # It is NOT, by itself, the container's runtime HTTPS_PROXY: that is
        # derived separately (bclaunch-3q12) to point at the :14322 MITM proxy.
        explicit_broker = agent_vault_broker or os.environ.get(
            AGENT_VAULT_BROKER_ENV
        )
        broker_address = explicit_broker or DEFAULT_AGENT_VAULT_BROKER

        # Build environment
        env: dict[str, str] = {}
        env["HOME"] = "/home/vscode"

        # --- Operator-supplied agent-vault credentials (bclaunch-5hi) ---
        # The in-container `agent-vault run` client authenticates to the broker
        # with an addr + token + vault triple.  These are OPERATOR-SUPPLIED at
        # launch (explicit launch() params, sourced from the CLI --env-file /
        # flags / process env).  Each is injected only when supplied; the token
        # value is NEVER a literal baked into source.  Each value also falls
        # back to the like-named process-env var so an operator who exports
        # AGENT_VAULT_* (e.g. via --env-file piped into the launcher's own env)
        # does not have to also pass an explicit flag.
        resolved_av_addr = agent_vault_addr or os.environ.get(AGENT_VAULT_ADDR_ENV)
        resolved_av_token = agent_vault_token or os.environ.get(AGENT_VAULT_TOKEN_ENV)
        resolved_av_vault = agent_vault_vault or os.environ.get(AGENT_VAULT_VAULT_ENV)
        if resolved_av_addr:
            env[AGENT_VAULT_ADDR_ENV] = resolved_av_addr
        if resolved_av_token:
            env[AGENT_VAULT_TOKEN_ENV] = resolved_av_token
        if resolved_av_vault:
            env[AGENT_VAULT_VAULT_ENV] = resolved_av_vault

        # --- Container runtime HTTPS_PROXY (bclaunch-3q12) ---
        # Route the agent's outbound HTTPS through the broker's MITM proxy
        # (:14322) so the broker substitutes the real Claude OAuth and GitHub
        # credentials; the container itself carries none.  Precedence: an
        # explicit operator-supplied broker URL (--agent-vault-broker /
        # BCLAUNCHER_AGENT_VAULT_BROKER) wins verbatim; otherwise the proxy is
        # DERIVED (:14322 + <token>:<vault> userinfo) from the env-file
        # AGENT_VAULT_ADDR/TOKEN/VAULT triple — the SAME derivation the brokered
        # clone uses.  Pre-3q12 this was set to the bare control-API address
        # (:14321), which is not an HTTPS-CONNECT MITM proxy, so the agent's
        # brokered calls failed (CONNECT tunnel failed / 405).
        runtime_proxy = _build_runtime_proxy_url(
            explicit_broker,
            resolved_av_addr,
            resolved_av_token,
            resolved_av_vault,
        )
        if runtime_proxy is not None:
            env[AGENT_VAULT_PROXY_ENV] = runtime_proxy
        else:
            # Neither an explicit broker nor a complete addr/token/vault triple
            # was supplied: fall back to the control-API default so the env var
            # is still present (e.g. for the HEALTHCHECK probe target), matching
            # the pre-3q12 behaviour for the no-credentials case.
            env[AGENT_VAULT_PROXY_ENV] = broker_address

        # --- Broker CA via env var (bclaunch-7pf REVISED) ---
        # The operator supplies the PUBLIC broker CA PEM as an AGENT_VAULT_CA_PEM
        # line in --env-file (sourced into the launcher's process env).  Carry
        # it through into the container env so the bc-base entrypoint
        # (bclaunch-9rr) can materialize it to a file and export the TLS-trust
        # vars.  No CA bind-mount and no controller-side trust env are built —
        # the controller does ZERO CA handling beyond this pass-through.
        #
        # General rule: inject every AGENT_VAULT_* key present in the process
        # env into the container env (without clobbering an explicit-param
        # value set above), keeping the "token never baked / operator-supplied"
        # property — the launcher only forwards what the operator supplied.
        for key, value in os.environ.items():
            if key.startswith("AGENT_VAULT_") and key not in env:
                env[key] = value

        if shopmsg_dsn:
            env[SHOPMSG_DSN_ENV] = shopmsg_dsn
        elif dsn := os.environ.get(SHOPMSG_DSN_ENV):
            env[SHOPMSG_DSN_ENV] = dsn

        # --- SHOPMSG_SYSTEM_SLUG injection (lead-53y0) ---
        # RESOLVE the product slug and INJECT it into the launched container's
        # docker run env as -e SHOPMSG_SYSTEM_SLUG=<resolved>, MIRRORING the
        # SHOPMSG_DSN injection idiom directly above (env-dict entry -> the
        # FakeDockerDriver records it as a -e flag on the recorded run command).
        # bc-launcher NEVER reads/consumes SHOPMSG_SYSTEM_SLUG itself; the
        # CONSUMER is the BC's own shop-msg at runtime (messaging, lead-tgsb).
        #
        # Precedence for the injected slug (this surface's own override on top
        # of the shared manifest-product middle tier):
        #   SHOPMSG_SYSTEM_SLUG env on the launcher invocation
        #     > manifest product:
        #     > DEFAULT_SYSTEM_SLUG ('shopsystem').
        if env_system_slug := os.environ.get(SHOPMSG_SYSTEM_SLUG_ENV):
            resolved_system_slug = env_system_slug
        elif manifest_product:
            resolved_system_slug = manifest_product
        else:
            resolved_system_slug = DEFAULT_SYSTEM_SLUG
        env[SHOPMSG_SYSTEM_SLUG_ENV] = resolved_system_slug

        # --- Readiness PROBE broker address (lead-cs7k DEFECT (b)) ---
        # The agent-vault broker the READINESS PROBE targets must be reachable
        # from the launched container's product network.  Derive it from the
        # SAME resolved product slug injected above (so a second product probes
        # ``<slug>-agent-vault`` rather than the hardcoded ``agent-vault``),
        # DECOUPLED from the runtime HTTPS_PROXY built earlier (so this never
        # clobbers the token:vault@host:14322 derived proxy).  An explicit
        # operator broker still wins verbatim.
        probe_broker_address = resolve_probe_broker_address(
            explicit_broker, resolved_system_slug
        )

        # --- Mounts (bclaunch-7pf REVISED: ZERO credential/CA bind mounts) ---
        # The placeholder .credentials.json is BAKED INTO the bc-base image
        # (no controller mount) and the broker CA travels as AGENT_VAULT_CA_PEM
        # (no controller mount).  The ONLY conditional mount remaining is the
        # SHOPMSG unix-socket mount below, which is functional transport, not
        # credential coupling.  Each entry: (type, source, dest, readonly).
        mounts: list[tuple[str, str, str, bool]] = []

        # --- workspace-mount (lead-zxtk, @scenario_hash:0bc8e4532c04bf72 /
        #     9fc84c8424b2a223) ---
        # When the operator supplies an existing host working tree via
        # ``workspace_mount``, bind-mount that host path at the container's
        # /workspace and SKIP the clone (and ALL clone-path provisioning: no
        # bd bootstrap, no shop-templates re-pour).  This presents the live
        # host tree unchanged inside the container — its committed `.beads`
        # registry and poured `.claude/skills` are left byte-for-byte intact
        # because no provisioning step writes to the mounted tree.  The clone
        # block below is gated on ``repo_url and not workspace_mount`` so a
        # workspace-mount launch never reaches it.
        if workspace_mount:
            mounts.append(("bind", workspace_mount, CONTAINER_WORKSPACE, False))

        # --- opt-in lead-only docker-socket mount (lead-zxtk,
        #     @scenario_hash:ff370a4e7e9dac5e / e177655ba09a73fa) ---
        # The host docker socket is bind-mounted into the container ONLY when
        # the opt-in flag is enabled (a lead-only capability that lets the
        # launched shop drive docker itself).  By default the flag is absent
        # and NO docker-socket mount is added, so an ordinary BC container
        # carries no access to the host docker daemon.
        # lead-wdvx (Bug 1): the bind-mount alone grants NO usable access — the
        # container's non-root default user is not in the host socket's owning
        # group, so every docker call inside the container is rejected
        # permission-denied.  Resolve the HOST socket's actual gid (it varies
        # by host) and add it to the container's supplementary groups via
        # ``--group-add`` so the mounted socket is actually usable.  This is
        # gated on the SAME opt-in flag as the mount, so a launch WITHOUT the
        # flag grants no docker-socket group (guard against over-grant).
        docker_socket_group_add: list[str] = []
        if mount_docker_socket:
            mounts.append(
                ("bind", DOCKER_SOCKET_PATH, DOCKER_SOCKET_PATH, False)
            )
            resolver = getattr(self._driver, "host_socket_gid", None)
            gid = resolver(DOCKER_SOCKET_PATH) if callable(resolver) else None
            if gid is not None:
                docker_socket_group_add.append(str(gid))

        # SHOPMSG_DSN may be a postgres DSN (no socket mount needed) or a
        # unix socket path.  If the DSN value looks like a socket file, add a
        # bind mount for it.
        dsn_value = env.get(SHOPMSG_DSN_ENV, "")
        if dsn_value.startswith("/") and not dsn_value.startswith("//"):
            # It's a host socket path — mount the containing directory
            socket_dir = os.path.dirname(dsn_value)
            mounts.append(("bind", socket_dir, socket_dir, False))

        # Launch-image source resolution.
        #
        # Precedence (mirrors the SHOPMSG_DSN idiom above: flag -> env ->
        # default): an explicit ``image`` param (the --image flag) wins;
        # otherwise the BC_IMAGE process-env var; otherwise the built-in
        # BC_IMAGE constant.  This lets a launch target a base image other
        # than the hard-coded default without editing source, while leaving
        # the default behaviour unchanged when neither flag nor env is set.
        if image:
            resolved_image = image
        elif env_image := os.environ.get(BC_IMAGE_ENV):
            resolved_image = env_image
        else:
            resolved_image = BC_IMAGE

        # Digest-resolution step (scenario af2f03d3ac519cb5).
        #
        # Before starting the container, resolve the resolved image tag's
        # CURRENT registry digest and run from that digest, so a republished
        # image reaches the new container instead of a stale locally-cached
        # "latest".  Without this step, a container started from the bare
        # "latest" tag would run whatever digest the local Docker cache holds
        # under "latest" (D_old) even after the registry has moved "latest"
        # to a newer digest (D_new).  When no registry driver is injected the
        # behaviour is unchanged: launch runs from the resolved image.
        launch_image = resolved_image
        if self._registry_driver is not None:
            resolved_digest = self._registry_driver.resolve_digest(resolved_image)
            # Pin the run to the resolved digest.  A bare digest (sha256:...)
            # is turned into a fully-qualified digest reference against the
            # resolved image's repository; an already-qualified reference is
            # used as-is.
            if resolved_digest.startswith("sha256:"):
                repo = resolved_image.split(":", 1)[0]
                launch_image = f"{repo}@{resolved_digest}"
            else:
                launch_image = resolved_digest
            # Freshness at launch (af2f03d3ac519cb5): PULL the resolved digest
            # into the local cache BEFORE starting the container.  Resolving
            # alone is not enough — if the republished digest (D_new) is not
            # fetched, a run can still serve whatever content the local cache
            # holds under the moving "latest" tag (D_old).  Pulling the
            # digest-pinned reference guarantees the new container runs from
            # D_new, the republished image, rather than the stale cached D_old.
            self._driver.pull(launch_image)

        self._driver.run(
            container_name=container,
            image=launch_image,
            env=env,
            mounts=mounts,
            network=resolved_network,
            detach=True,
            group_add=docker_socket_group_add or None,
        )

        out_lines: list[str] = [f"Started container {container}\n"]
        err_lines: list[str] = []
        if out_lines_remote:
            out_lines.append(out_lines_remote)

        # ADR-026: NO host gitconfig or .claude.json is copied into the
        # container.  GitHub and git identity flow through the agent-vault
        # broker on outbound requests; the only Claude credential file present
        # is the placeholder .credentials.json mounted read-only above.

        # Clone repository if URL provided AND no workspace-mount is in effect.
        # lead-zxtk: a workspace-mount launch bind-mounts an existing host tree
        # at /workspace and must SKIP the clone AND all clone-path provisioning
        # (bd bootstrap, shop-templates re-pour) so the mounted tree's
        # `.beads`/`.claude/skills` stay byte-unchanged.  The entire clone +
        # provisioning block lives under this guard, so when workspace_mount is
        # set the launch proceeds straight to agent-start against the mounted
        # tree.
        if repo_url and not workspace_mount:
            # --- Launch-time clone trust env (bclaunch-5fji) ---
            # DEFECT 1: route the clone's HTTPS through the broker's MITM proxy
            # (:14322 with token:vault basic-auth) — NOT the bare control-API
            # address (:14321) that the container's HTTPS_PROXY env carries.
            # DEFECT 2: the clone runs in a non-login shell that never sources
            # /etc/profile.d/agent-vault-ca.sh, so set GIT_SSL_CAINFO explicitly
            # to the container CA path the bc-base entrypoint materializes.
            # Both are passed on the clone exec's own environment so the
            # brokered auto-clone succeeds without the operator-side
            # --agent-vault-broker full-URL workaround.
            clone_env: dict[str, str] = {}
            clone_proxy_url = _build_clone_proxy_url(
                resolved_av_addr, resolved_av_token, resolved_av_vault
            )
            if clone_proxy_url:
                clone_env[AGENT_VAULT_PROXY_ENV] = clone_proxy_url
                # http_proxy/https_proxy lowercase variants are honoured by some
                # libcurl/git builds; set the canonical HTTPS_PROXY plus the
                # lowercase https_proxy so the clone routes regardless.
                clone_env["https_proxy"] = clone_proxy_url

            # --- FACET 3 (lead-z0v2 — supersedes scenario 0d29c76818a323a1
            #     with scenario 09f871cf8b99a34b):
            #     ACTUALLY write the broker MITM root CA content BEFORE the
            #     clone, then point git at the SAME existing path.
            # The clone routes through HTTPS_PROXY at the agent-vault MITM proxy
            # (:14322), so container git must trust the broker's MITM root CA at
            # CLONE time — not only at agent-run time.
            #
            # REGRESSION (lead-z0v2): the prior implementation (a) ran the
            # entrypoint materializer (agent-vault-ca.sh), which writes the CA
            # file ONLY when AGENT_VAULT_CA_PEM is set, and (b) UNCONDITIONALLY
            # set GIT_SSL_CAINFO to the CA path.  In a real flagless launch
            # AGENT_VAULT_CA_PEM was EMPTY, so nothing was written, yet git was
            # still pointed at the path — the clone failed
            # "error setting certificate file: .../ca.pem".  That is a
            # write-path-vs-trust-path MISMATCH.
            #
            # FIX: run a single clone-prep script that writes real CA *content*
            # to AGENT_VAULT_CONTAINER_CA_PATH (from AGENT_VAULT_CA_PEM when
            # present, else `agent-vault ca fetch`) and verifies it is a
            # non-empty PEM (first line "-----BEGIN CERTIFICATE-----").  Only
            # when the file is confirmed present do we point GIT_SSL_CAINFO at
            # that SAME path.  If the prep fails, the launch fails LOUDLY rather
            # than handing git a CA path that does not exist.  Run as root so
            # the script can create the trust dir regardless of prior ownership.
            ca_prep = self._driver.exec_run(
                container,
                ["/bin/sh", "-c", _clone_ca_materialize_script()],
            )
            if ca_prep.returncode != 0:
                return CommandResult(
                    exit_code=1,
                    stdout="".join(out_lines),
                    stderr=(
                        "agent-vault broker CA materialization failed before "
                        "the clone; refusing to point git at a CA path that "
                        f"does not exist ({AGENT_VAULT_CONTAINER_CA_PATH}): "
                        f"{ca_prep.stderr}"
                    ),
                )
            # write-path == trust-path: point git at the SAME path we just wrote
            # and verified.  Never set this for a path the prep did not produce.
            clone_env[GIT_SSL_CAINFO_ENV] = AGENT_VAULT_CONTAINER_CA_PATH
            out_lines.append(
                "Materialized the agent-vault broker MITM root CA content to "
                f"{AGENT_VAULT_CONTAINER_CA_PATH} and pointed git at that same "
                "existing path before the clone (lead-z0v2 FACET 3)\n"
            )

            # --- FACET 2 (lead-uiwu, scenario 4154b0ea63d0516b):
            #     /workspace owned by the agent user BEFORE the clone.
            # REGRESSION FIX: in the v0.3.33 image /workspace is created
            # root:root (Dockerfile WORKDIR), while the agent + clone run as the
            # unprivileged vscode user (uid 1000).  A clone performed as vscode
            # into a root-owned /workspace fails "git clone failed: ...
            # /workspace/.git: Permission denied".  The working messaging
            # container has /workspace owned vscode:vscode.  So chown /workspace
            # to vscode FIRST (as root), THEN perform the clone AS vscode so the
            # non-root clone writes /workspace/.git without Permission denied.
            # This makes the operator workaround (`docker exec -u root chown
            # vscode:vscode /workspace` then clone as vscode) durable.
            # Recursive (-R) to coexist with the lead-d64 invariant that EVERY
            # /workspace chown is recursive; /workspace is empty at this
            # pre-clone point, so -R is harmless here while still delivering the
            # FACET 2 ownership precondition.
            self._driver.exec_run(
                container,
                ["chown", "-R",
                 f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
                 CONTAINER_WORKSPACE],
            )
            out_lines.append(
                f"Chowned {CONTAINER_WORKSPACE} to {AGENT_CONTAINER_USER} "
                "before the clone (lead-uiwu FACET 2)\n"
            )

            clone_result = self._driver.exec_run(
                container,
                ["git", "clone", repo_url, CONTAINER_WORKSPACE],
                user=AGENT_CONTAINER_USER,
                env=clone_env,
            )
            if clone_result.returncode != 0:
                return CommandResult(
                    exit_code=1,
                    stdout="".join(out_lines),
                    stderr=f"git clone failed: {clone_result.stderr}",
                )
            out_lines.append(f"Cloned {repo_url} into {CONTAINER_WORKSPACE}\n")

            # Provision the in-container beads tracker so the BC boots
            # WRITE-READY with NO manual heal (lead-ezzr — SUPERSEDES the
            # lead-kjv7 pull+config+import mechanism).
            #
            # A freshly cloned BC lands WEDGED: `.beads/issues.jsonl` is
            # git-tracked at HEAD but may be ABSENT from the working tree
            # (a gitignore hook), the Dolt working set is empty (no
            # `embeddeddolt/`), and no usable issue_prefix is set — so
            # `bd ready` / `bd create` fail.
            #
            # lead-ezzr ROOT CAUSE + FIX.  The lead-kjv7 mechanism
            # (`bd dolt pull` → `bd config set issue_prefix` → `bd import`)
            # was EMPIRICALLY BROKEN: the launched BC came up with
            # issue_prefix '(not set)' and `bd create` failed "database not
            # initialized: issue_prefix config is missing".  The explicit
            # `bd import` pushed embedded-Dolt into the lead-vlsu deadlock
            # ('database already exists' + no prefix) where every documented
            # non-destructive recovery refuses.  The deadlock was
            # SELF-INFLICTED: `bd dolt pull` FIRST creates an empty local DB
            # that makes a later `bd bootstrap` say "already exists, nothing
            # to do".
            #
            # PROVEN RECIPE (lead-verified in a real v0.2.7 container, per
            # bd's own `bd help init-safety` "ADOPTING A REMOTE ... use
            # `bd bootstrap`"): on a fresh clone with committed
            # `.beads/issues.jsonl` present and NO pre-existing bd-created
            # Dolt working set, `bd bootstrap` imports the git-tracked JSONL,
            # creates `.beads/embeddeddolt/`, and `bd create` / `bd ready`
            # then SUCCEED.  Fully NON-DESTRUCTIVE.  So:
            #
            #   * Do NOT run `bd dolt pull` first (it pre-creates the empty DB
            #     that deadlocks bootstrap).
            #   * Do NOT `bd config set issue_prefix` (bd rejects it; bootstrap
            #     derives the prefix from the imported registry).
            #   * Do NOT run a separate `bd import` that pre-creates the DB
            #     (that is the wedged lead-vlsu path).
            #
            # (1) Ensure the committed registry is present in the working
            # tree.  A git clone normally provides it, but a gitignore hook
            # can leave it absent; `git checkout HEAD -- .beads/issues.jsonl`
            # materializes it FIRST so bootstrap has git-tracked JSONL to
            # import.
            # ORDER IS LOAD-BEARING (lead-d64, empirically proven by real
            # container launch): chown the ENTIRE workspace to vscode FIRST,
            # then run BOTH the materialize and `bd bootstrap` AS VSCODE.
            #
            # The clone ran as root, so `.git` and the tree are root-owned.
            # Running `bd bootstrap` as ROOT corrupts the repo: it leaves the
            # working set + `.git` internals (e.g. `.git/logs/HEAD`) root-owned,
            # and the subsequent vscode agent's git operations then fail with
            # "Permission denied", collapsing the index into dozens of phantom
            # staged deletions of `.beads/*` and `.claude/*` and removing the
            # `.beads` tree entirely — bd ends up NON-write-ready.  Chowning
            # only `.beads`, or chowning AFTER a root-run bootstrap, does NOT
            # fix this.  Proven-clean recipe: chown-whole-workspace-FIRST +
            # materialize-and-bootstrap-AS-VSCODE → 0 phantom deletions,
            # write-ready bd, `.git` writable by the agent.

            # (1) Hand the ENTIRE workspace (including `.git`) to vscode.
            self._driver.exec_run(
                container,
                ["chown", "-R",
                 f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
                 CONTAINER_WORKSPACE],
            )
            out_lines.append(
                f"Chowned {CONTAINER_WORKSPACE} (including .git) to "
                f"{AGENT_CONTAINER_USER}\n"
            )

            # (2) Mark the workspace a safe git directory for the agent user
            # (its ownership just changed), then materialize the committed
            # registry AS VSCODE so bootstrap has git-tracked JSONL to import.
            self._driver.exec_run(
                container,
                ["git", "config", "--global", "--add",
                 "safe.directory", CONTAINER_WORKSPACE],
                user=AGENT_CONTAINER_USER,
            )
            self._driver.exec_run(
                container,
                ["git", "-C", CONTAINER_WORKSPACE,
                 "checkout", "HEAD", "--", ".beads/issues.jsonl"],
                user=AGENT_CONTAINER_USER,
            )
            out_lines.append(
                "Materialized committed .beads/issues.jsonl into the "
                "working tree\n"
            )

            # (3) `bd bootstrap` AS VSCODE — imports the git-tracked JSONL,
            # creates `.beads/embeddeddolt/`, leaves the BC write-ready.  The
            # ONLY provisioning command; no `bd dolt pull` before it (would
            # wedge it into a no-op), no `bd config set` (bd rejects it), no
            # separate `bd import`.  Run as vscode (NOT root) so the working
            # set and any git ops land vscode-owned and the repo stays clean.
            #
            # DEFENSIVE re-chown immediately before bootstrap: empirically,
            # `.beads` can be observed root-owned at bootstrap time in a real
            # launch even though step (1) chowned the whole tree (a re-root
            # whose cause does not reproduce in isolated exec replays).  bd
            # bootstrap run as vscode must `mkdir .beads/embeddeddolt`, which
            # fails "permission denied" on a root-owned `.beads`.  Re-asserting
            # vscode ownership here makes the step robust regardless of cause.
            self._driver.exec_run(
                container,
                ["chown", "-R",
                 f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
                 CONTAINER_WORKSPACE],
            )
            boot_result = self._driver.exec_run(
                container,
                ["bash", "-lc",
                 f"cd {CONTAINER_WORKSPACE} && bd bootstrap"],
                user=AGENT_CONTAINER_USER,
            )

            # lead-5k8c — EMPTY-REMOTE PROVISIONING.  A BC whose `<bc>-beads`
            # Dolt remote was never seeded (the GitHub repo exists but is
            # EMPTY) makes `bd bootstrap`'s clone fail:
            #   "dolt clone git+https://.../<bc>-beads.git: git remote has no
            #    branches: cannot push...; initialize the repository with an
            #    initial branch/commit first".
            # Empirically observed live 2026-06-22 (lead-4qpq fleet relaunch):
            # this failure stranded a healthy cloned container with no agent.
            # The fix is to INITIALIZE the empty remote — seed it with an
            # initial branch/commit from the git-tracked `.beads/issues.jsonl`
            # — then retry bootstrap, INSTEAD of fatal-failing the launch.
            # The seed mirrors the heal performed live: `git init -b main` a
            # temp repo and push an initial commit to the `<bc>-beads.git`
            # GitHub repo (creds injected by the agent-vault proxy via
            # HTTPS_PROXY), then `bd dolt remote add origin` + `bd dolt push`.
            if boot_result.returncode != 0 and _is_empty_remote_failure(
                boot_result.stderr or boot_result.stdout or ""
            ):
                beads_remote = _beads_dolt_remote_url(bc_name)
                seed_result = self._driver.exec_run(
                    container,
                    ["bash", "-lc", _empty_remote_seed_script(beads_remote)],
                    user=AGENT_CONTAINER_USER,
                )
                if seed_result.returncode == 0:
                    out_lines.append(
                        "Empty beads dolt remote detected; initialized it with "
                        f"an initial branch/commit ({beads_remote}) and retried "
                        "bd bootstrap (lead-5k8c)\n"
                    )
                    # Re-assert vscode ownership before the retry (the seed may
                    # have re-rooted paths under .beads), then retry bootstrap.
                    self._driver.exec_run(
                        container,
                        ["chown", "-R",
                         f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
                         CONTAINER_WORKSPACE],
                    )
                    boot_result = self._driver.exec_run(
                        container,
                        ["bash", "-lc",
                         f"cd {CONTAINER_WORKSPACE} && bd bootstrap"],
                        user=AGENT_CONTAINER_USER,
                    )
                else:
                    err_lines.append(
                        "warning: beads dolt remote is empty and could not be "
                        f"initialized ({beads_remote}, exit "
                        f"{seed_result.returncode}): "
                        f"{(seed_result.stderr or seed_result.stdout).strip()}; "
                        "proceeding to agent-start so the agent can self-heal "
                        "(lead-5k8c)\n"
                    )

            if boot_result.returncode != 0:
                # lead-5k8c — NO PRE-AGENT-START STEP MAY FATAL-STRAND THE
                # CONTAINER.  This extends the lead-k4k7 warn-and-continue
                # pattern (originally applied to the shop-templates
                # skill-refresh) to the bd-bootstrap step.  A failed
                # `bd bootstrap` used to `return CommandResult(exit_code=1)`
                # BEFORE the tmux/claude agent-start step, leaving a fully
                # cloned "Up (healthy)" container with NO agent — a
                # non-resumable strand (observed live 2026-06-22).  Beads
                # provisioning is a boot convenience, NOT a precondition for
                # the agent to run: the BC's session-start beads-health step
                # self-heals a wedged tracker.  So a bootstrap failure now
                # WARNS and PROCEEDS to agent-start instead of aborting, so a
                # healthy cloned container is NEVER left without an agent.
                err_lines.append(
                    "warning: bd bootstrap failed while provisioning the "
                    "in-container bd working set (exit "
                    f"{boot_result.returncode}): "
                    f"{(boot_result.stderr or boot_result.stdout).strip()}; "
                    "the beads tracker may need a session-start heal but the "
                    "agent will still be started (lead-5k8c)\n"
                )
            else:
                out_lines.append(
                    "Ran bd bootstrap (imported git-tracked .beads/issues.jsonl "
                    "into the embedded-Dolt working set)\n"
                )

            # shop-templates skill-refresh (lead-dlrx scenario
            # 75ae95be0ecf1640; lead-q5k7 bugfix).
            #
            # After the repository has been cloned (and the beads/ownership
            # setup steps have run), re-pour the shop-templates skill-group
            # OVER the cloned workspace's committed `.claude/skills/` so the
            # launched shop carries the CURRENT package skills from first boot
            # (e.g. the lead-80t0 beads-health step), OVERWRITING any stale
            # committed copy in the BC's own repo.
            #
            # lead-q5k7 ROOT CAUSE + FIX.  The prior invocation execed
            # `shop-templates pour --workspace <ws>`, but `shop-templates`
            # has NO `pour` subcommand (valid: list/show/bootstrap/update)
            # and the flag is `--target`, not `--workspace`.  That exec
            # FAILED on every launch, yet the step appended a "Poured ..."
            # success line WITHOUT checking the result — a false-success log
            # that hid the failure, so the refresh silently NEVER ran and a
            # launched BC kept its committed-stale `.claude/skills/`.
            #
            # The correct invocation in the bc-base image is
            # `shop-templates update --target <ws> --shop-type <bc|lead>`.
            # The shop-type is derived from the cloned shop's own
            # `.claude/shop/type.md` (the canonical per-shop marker:
            # contents "bc" or "lead"); it must match the original
            # bootstrap.  Run as vscode so the refreshed files are owned by
            # the agent user (the chown above handed /workspace to vscode).
            # DEFENSIVE re-chown before the refresh: `shop-templates update`
            # runs as vscode and OVERWRITES `.claude/...` files (e.g.
            # canonical/bc-primer.md); a root-owned committed copy makes that
            # write fail "permission denied".  Re-assert vscode ownership so
            # the refresh can overwrite, regardless of any re-root upstream.
            self._driver.exec_run(
                container,
                ["chown", "-R",
                 f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
                 CONTAINER_WORKSPACE],
            )
            shop_type = self._read_shop_type(container)
            refresh_result = self._driver.exec_run(
                container,
                ["shop-templates", "update",
                 "--target", CONTAINER_WORKSPACE,
                 "--shop-type", shop_type],
                user=AGENT_CONTAINER_USER,
            )
            if refresh_result.returncode != 0:
                # lead-k4k7 — DOWNGRADE a skill-refresh failure from a fatal
                # early-return to a WARNING that still PROCEEDS to agent-start.
                #
                # The skill-refresh is a freshness nicety, not a precondition
                # for the agent to run: a stale-but-present skill set is
                # strictly better than a healthy container with no agent at all.
                # Previously a transient (network-blip) non-zero exit here did
                # `return CommandResult(exit_code=1)` BEFORE the tmux/claude
                # start, leaving a fully-cloned "Up (healthy)" container with no
                # agent session — a non-resumable strand that every relaunch
                # re-clones and re-strands (observed live 2026-06-19, blocking
                # 8 dispatches).  We now WARN and fall through to agent-start.
                #
                # The lead-q5k7 criterion-B invariants are preserved: we still
                # CHECK the result (no false-success "Refreshed ..." line on a
                # failure), and a failed refresh still deposits NO skills.  The
                # only change is the disposition — warn-and-continue instead of
                # fatal-abort.
                err_lines.append(
                    "warning: shop-templates skill-refresh failed "
                    f"(shop-type={shop_type!r}, exit "
                    f"{refresh_result.returncode}): "
                    f"{refresh_result.stderr.strip()}; the skill set may be "
                    "stale but the agent will still be started "
                    "(lead-k4k7)\n"
                )
            else:
                out_lines.append(
                    f"Refreshed shop-templates skill-group "
                    f"(shop-type={shop_type}) into "
                    f"{CONTAINER_WORKSPACE}/.claude/skills/\n"
                )

            # Self-contained fabro loop def bundle placement (lead-h2bj — S2
            # def-bundle delivery, ADR-051).  Placed on EVERY clone launch
            # (tmux AND fabro) so the cloned container carries the def runnable
            # FROM THE DEF ALONE.  On the fabro path the ADDITIONAL wiring
            # (workflow.toml rewrite + shim + settings) runs OUTSIDE this guard
            # via _place_fabro_def_and_wiring(place_def=False) — see lead-ze4w
            # BUG#1 below.  Runs BEFORE the FINAL ownership assertion so the
            # final chown hands the freshly-placed .fabro/ tree to the agent.
            self._place_fabro_def_bundle(container, out_lines, err_lines)

            # FINAL ownership assertion (lead-mf15, scenario
            # @scenario_hash:d9e4ce60e03df361).  TIGHTENS the lead-d64 /
            # lead-ezzr chowns: those run BEFORE the last root-context
            # provisioning op (the shop-templates refresh just above), so a
            # path that op — or any root-context op the container fires around
            # it — creates/re-roots is left root-owned with no later chown to
            # correct it before the agent engages.  Observed twice 2026-06-18:
            # `.beads` cloned root-owned at bring-up and `.git/objects/7e/`
            # re-rooted mid-run, each requiring a host
            # `docker exec -u root chown -R 1000:1000`.
            #
            # This chown is the LAST thing the launcher does under /workspace
            # before starting the agent's tmux session, so the ownership
            # snapshot the vscode agent inherits is UNCONDITIONALLY
            # vscode-owned across EVERY agent-touched path (/workspace, .git,
            # .beads) regardless of any intermediate re-root — no host-side
            # chown is ever needed.  It runs as root (the default — no `-u`)
            # because transferring ownership of any root-owned path requires
            # root.  This is additive: the chown-whole-workspace-first recipe
            # and the .beads-vscode-owned pin (2904f3a905567b48) continue to
            # hold; this only adds a final assertion AFTER the last
            # provisioning write.
            self._driver.exec_run(
                container,
                ["chown", "-R",
                 f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
                 CONTAINER_WORKSPACE],
            )
            out_lines.append(
                f"Final ownership assertion: re-chowned {CONTAINER_WORKSPACE} "
                f"(including .git and .beads) to {AGENT_CONTAINER_USER} after "
                f"the last provisioning op, before starting the agent\n"
            )

        # FABRO orchestrator wiring placement (lead-vwib; lead-ze4w BUG#1).
        #
        # lead-ze4w BUG#1 ROOT CAUSE + FIX.  The fabro shim/settings wiring (and
        # on the workspace-mount path the def bundle itself) previously lived
        # INSIDE the `if repo_url and not workspace_mount:` clone-only guard, so
        # on a `--workspace-mount --orchestrator fabro` launch it was SKIPPED
        # entirely — yet `_start_agent_session` still ran `fabro engage`, which
        # failed because /workspace/.fabro never existed.  This block is HOISTED
        # OUT of the clone guard so the fabro def + wiring is placed on BOTH the
        # clone AND the workspace-mount paths (before the engage).  It is GATED
        # on `launch_path == LAUNCH_PATH_FABRO`, so the tmux default path — on
        # either clone or workspace-mount — is UNCHANGED (no fabro writes, no
        # placement, tree presented unchanged).
        #
        # place_def: on the CLONE path the def bundle was ALREADY placed inside
        # the clone guard (lead-h2bj, every clone launch), so we do NOT re-place
        # it here — only the fabro-specific wiring runs.  On the WORKSPACE-MOUNT
        # path the clone guard never ran, so the def bundle must be placed here
        # too.  Placement runs BEFORE the engage in `_start_agent_session`, so
        # the fabro server + run find the placed def + settings.
        if launch_path == LAUNCH_PATH_FABRO:
            already_placed = bool(repo_url and not workspace_mount)
            self._place_fabro_def_and_wiring(
                bc_name,
                container,
                work_id,
                out_lines,
                err_lines,
                place_def=not already_placed,
            )

        # Agent-start sequence (shared with `start_agent`, lead-k4k7).  This is
        # the EXACT sequence the recovery subcommand drives so a stranded
        # container can be brought to a running agent without re-cloning; the
        # two readiness barriers and the inject-after-ready ordering stay
        # behaviorally identical across launch and start-agent because they run
        # the same code.
        return self._start_agent_session(
            bc_name,
            container,
            startup_prompt,
            env.get(SHOPMSG_DSN_ENV),
            probe_broker_address,
            out_lines,
            err_lines,
            launch_path=launch_path,
            work_id=work_id,
        )

    # ------------------------------------------------------------------
    # FABRO def + wiring placement (lead-h2bj / lead-vwib; hoisted out of the
    # clone-only guard by lead-ze4w BUG#1 so it runs on the workspace-mount
    # path too)
    # ------------------------------------------------------------------

    def _place_fabro_def_bundle(
        self,
        container: str,
        out_lines: list[str],
        err_lines: list[str],
    ) -> None:
        """Place the 15 packaged fabro loop def-bundle asset files into the
        launched container at FABRO_DEF_CONTAINER_DIR (lead-h2bj, ADR-051).

        Placed via a single exec_run running a base64-decode script (the driver
        exposes no `docker cp` seam), so each file lands byte-identical to the
        shipped asset regardless of content.  NATIVE-VAULT INVARIANT (ADR-049):
        the placed vaults/default/secrets.json is the `__PLACEHOLDER__`-only
        asset; placement introduces no real secret.  Run as vscode so the def
        is agent-owned; a failed placement is a boot-convenience warning, not a
        fatal abort that strands a healthy container with no agent.
        """
        def_files = _load_fabro_def_files()
        def_place_result = self._driver.exec_run(
            container,
            ["/bin/sh", "-c", _fabro_def_install_script(def_files)],
            user=AGENT_CONTAINER_USER,
        )
        if def_place_result.returncode != 0:
            err_lines.append(
                "warning: fabro loop def-bundle placement failed (exit "
                f"{def_place_result.returncode}): "
                f"{(def_place_result.stderr or def_place_result.stdout).strip()}"
                f"; the container may lack {FABRO_DEF_CONTAINER_DIR} but the "
                "agent will still be started (lead-h2bj)\n"
            )
        else:
            out_lines.append(
                f"Placed the self-contained fabro loop def bundle "
                f"({len(FABRO_DEF_FILES)} files) into "
                f"{FABRO_DEF_CONTAINER_DIR} (lead-h2bj, ADR-051)\n"
            )

    def _place_fabro_def_and_wiring(
        self,
        bc_name: str,
        container: str,
        work_id: str | None,
        out_lines: list[str],
        err_lines: list[str],
        place_def: bool = True,
    ) -> None:
        """Place the fabro-orchestrator wiring (workflow.toml identity rewrite
        + shim start + effective settings) — and, when ``place_def`` is True,
        the def bundle itself — into the launched container.

        lead-ze4w BUG#1: this runs on BOTH the clone and the workspace-mount
        launch paths (the caller invokes it OUTSIDE the clone-only guard), so a
        `--workspace-mount --orchestrator fabro` launch actually has
        /workspace/.fabro before `_start_agent_session` runs `fabro engage`.
        It is only invoked on the fabro path, so the tmux default is unchanged.

        ``place_def``: on the CLONE path the def bundle is placed inside the
        clone guard (every clone launch), so the caller passes place_def=False
        to avoid re-placing it.  On the WORKSPACE-MOUNT path the clone guard
        never ran, so place_def=True places the def here too.

        Steps (each a boot-convenience warn-and-continue on failure, mirroring
        the def-placement disposition — never a fatal abort that strands a
        healthy container with no agent):

          (0) PLACE the def bundle (only when ``place_def``; lead-h2bj).
          (1) REWRITE the placed workflow.toml BC_NAME / WORK_ID to the
              launch's ACTUAL bc_name / work_id (lead-ze4w BUG#2).
          (2) START the baked so2h shim as a background listener, with
              SSL_CERT_FILE pinned (lead-ze4w BUG#3).
          (3) WRITE fabro's effective workflow-level settings.toml pointing the
              anthropic provider at the shim; NO credential (ADR-049 D1/D2).
          (4) chown the placed .fabro/ tree to the agent user so the placed def
              + writes are agent-owned (on the workspace-mount path this touches
              ONLY the launcher-created .fabro/ subtree, never the mounted
              tree's committed .beads / .claude).
        """
        # (0) Place the def bundle when it was not already placed by the clone
        #     guard (workspace-mount path, lead-ze4w BUG#1).
        if place_def:
            self._place_fabro_def_bundle(container, out_lines, err_lines)

        # (1) Rewrite the placed workflow.toml's BC_NAME / WORK_ID to the
        #     launch's ACTUAL identity (lead-ze4w BUG#2).  The packaged asset
        #     carries the bundle defaults (fabro-throwaway / fabro-spike-demo-3)
        #     in BOTH [run.inputs] and [run.environment.env]; the native
        #     script= nodes read $BC_NAME / $WORK_ID from the [run.environment.
        #     env] overlay, and `fabro run -I` overrides only [run.inputs] for
        #     agent prompts — so without this rewrite every native node runs
        #     against the bundle identity.  Modeled on the settings.toml
        #     (re)write mechanism (host-side byte generation + base64-decode
        #     over the placed path).
        workflow_result = self._driver.exec_run(
            container,
            ["/bin/sh", "-c",
             _fabro_workflow_toml_install_script(bc_name, work_id or "")],
            user=AGENT_CONTAINER_USER,
        )
        if workflow_result.returncode != 0:
            err_lines.append(
                "warning: fabro workflow.toml BC_NAME/WORK_ID rewrite failed "
                f"(exit {workflow_result.returncode}): "
                f"{(workflow_result.stderr or workflow_result.stdout).strip()}"
                f"; {FABRO_WORKFLOW_TOML_CONTAINER_PATH} may carry the bundle "
                "defaults but the agent will still be started (lead-ze4w)\n"
            )
        else:
            out_lines.append(
                "Rewrote the placed "
                f"{FABRO_WORKFLOW_TOML_CONTAINER_PATH} [run.environment.env] / "
                f"[run.inputs] BC_NAME={bc_name} WORK_ID={work_id or ''} "
                "(lead-ze4w BUG#2)\n"
            )

        # (2) Start the baked so2h shim as a background listener, with
        #     SSL_CERT_FILE pinned on the exec env (lead-ze4w BUG#3) so its
        #     urllib trusts the agent-vault MITM CA without a login shell.
        shim_result = self._driver.exec_run(
            container,
            ["/bin/sh", "-c", _fabro_shim_start_script()],
            user=AGENT_CONTAINER_USER,
            env=_fabro_exec_env(),
        )
        if shim_result.returncode != 0:
            err_lines.append(
                "warning: anthropic-oauth-shim start failed (exit "
                f"{shim_result.returncode}): "
                f"{(shim_result.stderr or shim_result.stdout).strip()}"
                f"; fabro's anthropic provider may lack a local "
                f"endpoint on {FABRO_SHIM_HOST}:{FABRO_SHIM_PORT} but "
                "the agent will still be started (lead-vwib)\n"
            )
        else:
            out_lines.append(
                "Started the baked anthropic-oauth-shim "
                f"({ANTHROPIC_OAUTH_SHIM_BIN}) as a background "
                f"listener on {FABRO_SHIM_HOST}:{FABRO_SHIM_PORT} "
                "(lead-vwib, lead-so2h)\n"
            )

        # (3) Write fabro's effective workflow-level settings pointing the
        #     anthropic provider at the shim; no credential written (ADR-049).
        settings_result = self._driver.exec_run(
            container,
            ["/bin/sh", "-c", _fabro_settings_install_script()],
            user=AGENT_CONTAINER_USER,
        )
        if settings_result.returncode != 0:
            err_lines.append(
                "warning: fabro effective-settings write failed (exit "
                f"{settings_result.returncode}): "
                f"{(settings_result.stderr or settings_result.stdout).strip()}"
                f"; {FABRO_SETTINGS_CONTAINER_PATH} may be missing but "
                "the agent will still be started (lead-vwib)\n"
            )
        else:
            out_lines.append(
                "Wrote fabro effective settings to "
                f"{FABRO_SETTINGS_CONTAINER_PATH} "
                f"([llm.providers.anthropic] base_url="
                f"{FABRO_ANTHROPIC_BASE_URL}, adapter="
                f"{FABRO_ANTHROPIC_ADAPTER}; no credential written — "
                "ADR-049 D1/D2)\n"
            )

        # (4) Hand the placed .fabro/ tree to the agent user.  On the
        #     workspace-mount path this deliberately scopes the chown to the
        #     launcher-created .fabro/ subtree ONLY — it does NOT recursively
        #     chown the mounted host tree's committed .beads / .claude (the
        #     lead-zxtk byte-unchanged invariant).
        self._driver.exec_run(
            container,
            ["chown", "-R",
             f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
             FABRO_DEF_CONTAINER_DIR],
        )
        out_lines.append(
            f"Chowned the placed {FABRO_DEF_CONTAINER_DIR} tree to "
            f"{AGENT_CONTAINER_USER} (lead-ze4w BUG#1 workspace-mount parity)\n"
        )

    # ------------------------------------------------------------------
    # agent-start sequence (shared by launch + start_agent, lead-k4k7)
    # ------------------------------------------------------------------

    def _write_launch_diagnostic(
        self,
        bc_name: str,
        cause_marker: str,
        reason: str,
        err_lines: list[str],
    ) -> Path | None:
        """Persist a launch-failure diagnostic FILE on the per-BC host surface.

        lead-63em.  Writes a single human-readable line carrying the literal
        ``cause_marker`` token plus ``reason`` to the documented per-BC
        host-discoverable path (``launch_diagnostic_path``).  The file is
        readable from the host WITHOUT attaching into any tmux session and
        WITHOUT relying on the launch command's stderr or the bc-container
        monitor tmux pane.

        lead-bnhn (P1 bugfix) — BEST-EFFORT / NON-FATAL.  The diagnostic write
        (its on-demand parent ``mkdir`` and the file write) is wrapped so that
        ANY write failure (``PermissionError`` / ``OSError`` from an unwritable
        target dir, a read-only filesystem, etc.) is CAUGHT here, surfaced as a
        host-discoverable WARNING on the launch result's stderr (naming that
        the diagnostic could NOT be written, the target path, and the cause),
        and then SWALLOWED so the launch is NOT aborted.  A diagnostic-write
        failure is strictly less severe than the launch failure it would
        describe; it must degrade gracefully, never escalate.  This method is
        the single choke point ALL launch-failure-diagnostic call sites pass
        through, so wrapping it here protects EVERY call site at once.

        On success the method itself appends the host-discoverable
        ``launch diagnostic persisted to <path>`` line to ``err_lines`` and
        returns the path written; on a caught write failure it appends the
        warning line and returns ``None``.  The FILE, not the stderr line, is
        the authoritative diagnostic surface when the write succeeds — but the
        stderr warning is the legible fallback when even the file cannot be
        written.
        """
        path = launch_diagnostic_path(bc_name)
        content = (
            f"cause: {cause_marker}\n"
            f"reason: {reason}\n"
        )
        try:
            self._driver.write_launch_diagnostic(str(path), content)
        except OSError as exc:
            # NON-FATAL: the diagnostic write failed (e.g. the target dir is
            # not writable — the lead-bnhn /var/lib/bc-launcher PermissionError
            # crash).  Surface a host-discoverable WARNING and CONTINUE; never
            # let the diagnostic-write failure abort the launch it describes.
            err_lines.append(
                f"warning: could not write launch diagnostic to {path}: "
                f"{type(exc).__name__}: {exc}; continuing without the "
                f"persisted diagnostic file (the launch failure cause is "
                f"reported on stderr above)\n"
            )
            return None
        err_lines.append(f"launch diagnostic persisted to {path}\n")
        return path

    def _fabro_engage(
        self,
        bc_name: str,
        container: str,
        dsn: str | None,
        probe_broker_address: str,
        work_id: str | None,
        out_lines: list[str],
        err_lines: list[str],
    ) -> CommandResult:
        """Drive the FABRO orchestrator ENGAGE step (lead-cadr — S4).

        REPLACES the tmux/claude engage tier on the fabro launch path (ADR-050
        D3): AFTER the SAME readiness barriers the tmux path gates on
        (messaging DB + agent-vault broker — ADR-050 D1/D2 launch parity), the
        launcher engages by

          1. starting the EPHEMERAL in-container fabro server in the
             FOREGROUND with no web UI, bound to a local 127.0.0.1 socket
             (``fabro server start --foreground --no-web``), so the loop runs
             headless inside the one bc-base container; and
          2. running the placed ADR-051 Implementer->Reviewer loop def against
             that server (``fabro run workflow.fabro -I BC_NAME=<bc> -I
             WORK_ID=<work_id>``) as the engage.

        It starts NO tmux ``agent`` send-keys session and NO ``claude`` engage
        — the engage tier is REPLACED by the fabro run-graph entry, not added
        alongside it (reproduces fabro-orchestration/01
        @scenario_hash:1aeace4c593ab14f via the real bc-container launch path).
        """
        # Readiness barrier — messaging database reachability (IDENTICAL to the
        # tmux path — ADR-050 D1/D2 launch parity).  On failure engage NOTHING.
        if dsn and not self._driver.messaging_db_reachable(
            dsn, container=container
        ):
            reason = (
                f"messaging readiness failure: messaging database at "
                f"{SHOPMSG_DSN_ENV}={dsn} is not reachable; fabro engage NOT "
                f"started"
            )
            err_lines.append(reason + "\n")
            self._write_launch_diagnostic(
                bc_name, CAUSE_MARKER_MESSAGING_DB, reason, err_lines
            )
            return CommandResult(
                exit_code=1,
                stdout="".join(out_lines),
                stderr="".join(err_lines),
            )

        # Readiness barrier — agent-vault broker reachability (IDENTICAL to the
        # tmux path — ADR-026 / ADR-050 D1/D2 launch parity).
        if not self._driver.agent_vault_reachable(
            probe_broker_address, container=container
        ):
            reason = (
                f"agent-vault readiness failure: agent-vault broker at "
                f"{probe_broker_address} is not reachable; fabro engage NOT "
                f"started"
            )
            err_lines.append(reason + "\n")
            self._write_launch_diagnostic(
                bc_name, CAUSE_MARKER_AGENT_VAULT, reason, err_lines
            )
            return CommandResult(
                exit_code=1,
                stdout="".join(out_lines),
                stderr="".join(err_lines),
            )

        # ENGAGE (REPLACES the tmux/claude engage — ADR-050 D3).  One `/bin/sh
        # -c` script cd's into the placed def dir, starts the ephemeral fabro
        # server (foreground, no web, provider=local, 127.0.0.1), then runs the
        # loop def against it carrying BC_NAME + WORK_ID.  Runs as the vscode
        # agent user (the def + settings were placed agent-owned).  NO tmux
        # session is created and NO `claude` is started on this path.
        engage_result = self._driver.exec_run(
            container,
            ["/bin/sh", "-c", _fabro_engage_script(bc_name, work_id or "")],
            user=AGENT_CONTAINER_USER,
            env=_fabro_exec_env(),
        )
        if engage_result.returncode != 0:
            reason = (
                f"fabro engage failure: `fabro server start` / `fabro run "
                f"{FABRO_WORKFLOW_FILE}` exited {engage_result.returncode}: "
                f"{(engage_result.stderr or engage_result.stdout).strip()}"
            )
            err_lines.append("warning: " + reason + "\n")
            return CommandResult(
                exit_code=1,
                stdout="".join(out_lines),
                stderr="".join(err_lines),
            )
        out_lines.append(
            "Fabro orchestrator engage (lead-cadr): started the ephemeral "
            f"in-container fabro server ({' '.join(_fabro_server_start_argv())}"
            ") and ran the ADR-051 loop def as the engage ("
            f"{' '.join(_fabro_run_argv(bc_name, work_id or ''))}); no tmux "
            "'agent' send-keys session and no 'claude' engage started on this "
            "path — the engage tier is REPLACED by the fabro run-graph entry "
            "(ADR-050 D3)\n"
        )
        return CommandResult(
            exit_code=0, stdout="".join(out_lines), stderr="".join(err_lines)
        )

    def _start_agent_session(
        self,
        bc_name: str,
        container: str,
        startup_prompt: str | None,
        dsn: str | None,
        probe_broker_address: str,
        out_lines: list[str],
        err_lines: list[str],
        launch_path: str = LAUNCH_PATH_TMUX,
        work_id: str | None = None,
    ) -> CommandResult:
        """Drive the agent-start sequence against an already-provisioned
        container: start the agent tmux session, gate on the two readiness
        barriers, start ``agent-vault run -- claude``, wait for the readiness
        markers, and inject the startup prompt.

        SHARED by ``launch`` (after clone + provisioning) and ``start_agent``
        (recovery against an already-cloned container).  Sharing the sequence
        keeps the readiness barriers and inject ordering identical across both
        entry points (lead-k4k7).  ``out_lines`` / ``err_lines`` accumulate the
        result's stdout / stderr; the caller passes whatever preamble it has
        already logged.

        ENGAGE TIER (lead-cadr — S4).  ``launch_path`` selects the engage tier
        AFTER the readiness barriers pass; the barriers themselves and every
        launch-parity surface (container / credential-proxy / postgres DSN /
        shop-msg mailbox) are IDENTICAL on both paths (ADR-050 D1/D2):

          * ``launch_path == "tmux"`` (DEFAULT): the EXISTING tmux ``agent``
            send-keys / ``agent-vault run -- claude`` engage, UNCHANGED
            (scenario 04, @scenario_hash:04236074a60ffcd7).  NO fabro server,
            NO fabro run.
          * ``launch_path == "fabro"``: the engage tier is REPLACED (ADR-050
            D3) by the fabro run-graph entry — the launcher starts the
            ephemeral in-container fabro server
            (``fabro server start --foreground --no-web``) and runs the placed
            ADR-051 loop def against it
            (``fabro run workflow.fabro -I BC_NAME=<bc> -I WORK_ID=<work_id>``)
            as the engage, and starts NO tmux ``agent`` send-keys session and
            NO ``claude`` engage on this path.

        ``work_id`` carries the WORK_ID into the fabro run's ``-I`` input; it
        is unused on the tmux path.
        """
        # FABRO ORCHESTRATOR ENGAGE (lead-cadr).  On the fabro path the engage
        # tier is REPLACED, not added alongside (ADR-050 D3): the launcher
        # starts NO tmux `agent` send-keys session and NO `claude` engage.
        # The readiness barriers still gate the engage (identical to the tmux
        # path — ADR-050 D1/D2 launch parity): on failure, engage NOTHING.
        if launch_path == LAUNCH_PATH_FABRO:
            return self._fabro_engage(
                bc_name,
                container,
                dsn,
                probe_broker_address,
                work_id,
                out_lines,
                err_lines,
            )

        # Start tmux session as vscode.  Claude Code refuses
        # --dangerously-skip-permissions when EUID==0 ("cannot be used with
        # root/sudo privileges for security reasons"), so the agent must
        # run as the unprivileged vscode user — and that requires the tmux
        # server itself to be vscode-owned, because tmux refuses
        # cross-user attach (any subsequent send-keys / capture-pane /
        # has-session / attach-session call against this session must
        # therefore also run as vscode).
        self._driver.exec_run(
            container,
            ["tmux", "new-session", "-d", "-s", AGENT_TMUX_SESSION],
            user=AGENT_CONTAINER_USER,
        )
        out_lines.append(f"Started tmux session '{AGENT_TMUX_SESSION}'\n")

        # Start Claude Code inside the tmux session and wait for readiness
        # before injecting any user prompt.  The default tmux session command
        # is bash; without this sequence the startup prompt lands in bash
        # ("-bash: Run: command not found") and Claude Code never starts.
        # Only run the readiness sequence when a startup_prompt will be
        # injected.  An empty startup_prompt (lead-9sq's documented opt-out)
        # skips both the prompt injection AND the Claude Code start, leaving
        # the tmux session with its default bash command — preserving the
        # legacy escape hatch.
        if startup_prompt:
            # Readiness barrier — messaging database reachability.
            #
            # Before engaging the agent we verify the messaging backend at
            # SHOPMSG_DSN is reachable.  A BC agent whose messaging DB is
            # unreachable cannot arm its inbox watcher or drain pending
            # inbox, so injecting the startup prompt would launch an agent
            # straight into a wall of connection failures.  This barrier
            # fires BEFORE any Claude Code start / prompt injection: on
            # failure we return non-zero with a stderr line naming the DSN
            # and send NOTHING to the tmux session.
            if dsn and not self._driver.messaging_db_reachable(
                dsn, container=container
            ):
                reason = (
                    f"messaging readiness failure: messaging database at "
                    f"{SHOPMSG_DSN_ENV}={dsn} is not reachable; "
                    f"startup prompt NOT injected"
                )
                err_lines.append(reason + "\n")
                self._write_launch_diagnostic(
                    bc_name, CAUSE_MARKER_MESSAGING_DB, reason, err_lines
                )
                return CommandResult(
                    exit_code=1,
                    stdout="".join(out_lines),
                    stderr="".join(err_lines),
                )

            # Readiness barrier — agent-vault broker reachability (ADR-026).
            #
            # The agent's Claude OAuth and GitHub credentials are substituted
            # by the agent-vault broker on outbound requests; an agent whose
            # broker is unreachable can authenticate to nothing.  This barrier
            # fires BEFORE any Claude Code start / prompt injection: on failure
            # we return non-zero with a stderr line naming the configured
            # broker address and send NOTHING to the tmux session.  Combined
            # with the messaging-DB barrier above, the agent engages only when
            # BOTH the messaging database AND the agent-vault broker are
            # reachable (scenarios f73afae0 / 64aaff80 / 6cb07698).
            #
            # lead-cs7k: the probe targets ``probe_broker_address`` (derived
            # from the resolved product slug, decoupled from the runtime proxy)
            # and runs from INSIDE the launched container's network context
            # (``container=container``) so its reachability matches the
            # container's, not the launcher host's.
            if not self._driver.agent_vault_reachable(
                probe_broker_address, container=container
            ):
                reason = (
                    f"agent-vault readiness failure: agent-vault broker at "
                    f"{probe_broker_address} is not reachable; "
                    f"startup prompt NOT injected"
                )
                err_lines.append(reason + "\n")
                self._write_launch_diagnostic(
                    bc_name, CAUSE_MARKER_AGENT_VAULT, reason, err_lines
                )
                return CommandResult(
                    exit_code=1,
                    stdout="".join(out_lines),
                    stderr="".join(err_lines),
                )

            # Step 1: start Claude Code, wrapped as `agent-vault run -- claude`
            # (ADR-026).  agent-vault run establishes the proxy substitution
            # context (HTTPS_PROXY is already exported into the container env
            # pointing at the broker) so the broker injects the real Claude
            # OAuth / GitHub credentials on outbound requests; the container
            # holds only the placeholder.  --dangerously-skip-permissions is
            # passed through to claude: the BC container is the isolation
            # boundary the permission prompts substitute for, so bypassing
            # them inside the container prevents the agent from hanging on
            # permission gates that have no operator at the other end.
            self._driver.exec_run(
                container,
                ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION,
                 "agent-vault run -- claude --dangerously-skip-permissions",
                 "Enter"],
                user=AGENT_CONTAINER_USER,
            )
            # Step 2/3: bounded readiness wait that resolves the workspace-trust
            # gate by polling for EITHER of two markers (lead-gw9v / lead-c713),
            # integrated with — and feeding into — the step-4 input-ready loop:
            #
            #   * CLAUDE_READY_MARKER ("Accessing workspace:") — the PRE-trust
            #     banner that appears BEFORE trust is accepted.  When it is
            #     observed first, the trust prompt is live: accept it with a
            #     bare Enter (step 3) and fall through to the step-4 input-ready
            #     wait.  This is the pre-trust path and it is UNCHANGED.
            #
            #   * CLAUDE_INPUT_READY_MARKER ("bypass permissions on") — the
            #     POST-trust input-ready marker.  bc-base bakes
            #     `bypassPermissionsModeAccepted`, so claude can SELF-ADVANCE
            #     past the workspace-trust prompt straight to input-ready; the
            #     transient "Accessing workspace:" banner is then never caught
            #     by polling.  When the pane is ALREADY at input-ready, treat
            #     claude as UP: SKIP the trust-accept Enter (there is no trust
            #     prompt to accept) and proceed directly to inject — do NOT
            #     hard-require the transient banner and do NOT abort.
            #
            # The PRIOR shape hard-gated on CLAUDE_READY_MARKER and ABORTED with
            # an "agent-startup failure" the instant the transient banner was
            # not caught — which dropped every self-advancing unattended launch
            # even though claude was healthy and sitting at input-ready.  The
            # loop below removes that hard gate while keeping the pre-trust path
            # intact and bounding the whole wait by the readiness timeout.
            trust_accepted = False
            input_ready = False
            deadline = (
                self._monotonic() + CLAUDE_READINESS_TIMEOUT_SECONDS
            )
            while True:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    break
                per_attempt = min(
                    READINESS_DISMISS_POLL_SECONDS, remaining
                )
                # Poll for the PRE-trust banner first so the pre-trust path
                # (banner observed → accept trust with Enter) is unchanged.
                banner = self._driver.wait_for_pane_marker(
                    container,
                    AGENT_TMUX_SESSION,
                    CLAUDE_READY_MARKER,
                    per_attempt,
                )
                if banner:
                    # Step 3: accept the workspace-trust prompt (default "Yes, I
                    # trust").  Empirically verified (2026-05-29) that
                    # --dangerously-skip-permissions does NOT, on its own,
                    # bypass workspace trust when the prompt IS presented; this
                    # Enter advances past it.  It fires ONLY on the pre-trust
                    # path — never when claude self-advanced (below).
                    self._driver.exec_run(
                        container,
                        ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, "Enter"],
                        user=AGENT_CONTAINER_USER,
                    )
                    trust_accepted = True
                    break
                # Banner not caught this attempt.  Capture the pane: if claude
                # has SELF-ADVANCED past the trust prompt straight to the
                # input-ready marker, treat it as up and SKIP the trust-accept
                # Enter entirely.
                pane = self._driver.capture_pane(
                    container, AGENT_TMUX_SESSION
                )
                if CLAUDE_INPUT_READY_MARKER in pane:
                    input_ready = True
                    out_lines.append(
                        "Agent self-advanced past the workspace-trust prompt "
                        "to the input-ready marker "
                        f"{CLAUDE_INPUT_READY_MARKER!r}; treating the agent as "
                        "up and skipping the trust-accept Enter (lead-gw9v)\n"
                    )
                    break
                # Neither marker yet.  Keep polling until the deadline.
                if self._monotonic() >= deadline:
                    break
            if not trust_accepted and not input_ready:
                # Neither the PRE-trust banner nor the self-advanced input-ready
                # marker was reached within the readiness timeout: claude (or
                # its tmux session) never came up.  Warn (host-discoverable)
                # and abort WITHOUT injecting.
                reason = (
                    f"agent-startup failure: Claude Code did not become ready "
                    f"within {CLAUDE_READINESS_TIMEOUT_SECONDS:.0f}s — the agent "
                    f"never reached input-ready: neither the workspace-trust "
                    f"banner {CLAUDE_READY_MARKER!r} nor the input-ready marker "
                    f"{CLAUDE_INPUT_READY_MARKER!r} was observed within the "
                    f"readiness timeout (claude or its tmux session never "
                    f"started); startup prompt NOT injected"
                )
                err_lines.append("warning: " + reason + "\n")
                self._write_launch_diagnostic(
                    bc_name, CAUSE_MARKER_AGENT_STARTUP, reason, err_lines
                )
                return CommandResult(
                    exit_code=1,
                    stdout="".join(out_lines),
                    stderr="".join(err_lines),
                )
            # Step 4: wait for the POST-trust input-ready marker, with
            # bounded auto-dismissal of unexpected interactive prompts
            # (lead-cw7m / lead-c713).  SKIPPED when claude already
            # self-advanced to input-ready above (lead-gw9v).
            #
            # CLAUDE_INPUT_READY_MARKER is "bypass permissions on" — only
            # present once the trust prompt has cleared AND
            # --dangerously-skip-permissions is active, which is the exact
            # state in which the user prompt can be safely injected.
            #
            # The new bc-base Claude Code image (c50b3b) can render an EARLIER
            # interactive prompt (e.g. "Try the new fullscreen renderer?")
            # that BLOCKS reaching input-ready.  A single narrow wait would
            # time out at 60s and never inject.  Instead, run a BOUNDED
            # scan-dismiss loop: each iteration waits for the input-ready
            # marker for a short per-attempt budget; if it does not appear,
            # capture the pane and, if it presents an Esc-dismissable prompt
            # that is NOT the workspace-trust prompt, send ONLY Esc (decline —
            # NEVER Enter / '1', so the renderer is NOT enabled), emit a
            # host-discoverable WARNING NAMING the prompt, and continue.  The
            # TOTAL elapsed time is bounded by CLAUDE_READINESS_TIMEOUT_SECONDS;
            # on timeout WITHOUT input-ready the loop STOPS attempting
            # dismissals (no infinite loop), warns, and proceeds WITHOUT
            # injecting.
            #
            # lead-gw9v: when claude SELF-ADVANCED past the trust prompt (above),
            # input_ready is already True and the agent is already at the
            # input-ready marker — there is nothing left to wait for or dismiss,
            # so this whole loop is SKIPPED and we proceed straight to inject.
            if not input_ready:
                deadline = (
                    self._monotonic() + CLAUDE_READINESS_TIMEOUT_SECONDS
                )
                while True:
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        break
                    per_attempt = min(
                        READINESS_DISMISS_POLL_SECONDS, remaining
                    )
                    input_ready = self._driver.wait_for_pane_marker(
                        container,
                        AGENT_TMUX_SESSION,
                        CLAUDE_INPUT_READY_MARKER,
                        per_attempt,
                    )
                    if input_ready:
                        break
                    # Input-ready not yet observed within this attempt.  Capture
                    # the pane and look for a blocking readiness-wait prompt to
                    # auto-dismiss with Esc.
                    pane = self._driver.capture_pane(
                        container, AGENT_TMUX_SESSION
                    )
                    prompt_name = _readiness_wait_blocking_prompt(pane)
                    if prompt_name is None:
                        # No Esc-dismissable prompt is blocking; nothing more to
                        # do this iteration — keep polling until the deadline.
                        if self._monotonic() >= deadline:
                            break
                        continue
                    # Send a DISCRETE send-keys carrying ONLY the Escape key
                    # payload — NOT Enter, and NOT '1'.  This declines the
                    # prompt with its own non-committal default (e.g. does NOT
                    # enable the fullscreen renderer) and lets the loop proceed.
                    self._driver.exec_run(
                        container,
                        ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION,
                         ESCAPE_KEY_NAME],
                        user=AGENT_CONTAINER_USER,
                    )
                    err_lines.append(
                        "warning: an unexpected interactive prompt was "
                        "auto-dismissed during the readiness wait (sent Escape "
                        f"to the tmux session {AGENT_TMUX_SESSION!r}, NOT Enter, "
                        "so no default was confirmed and the fullscreen "
                        f"renderer was NOT enabled); the prompt was: "
                        f"{prompt_name!r} (lead-cw7m)\n"
                    )
                    out_lines.append(
                        "Auto-dismissed an unexpected interactive prompt with "
                        "Escape during the readiness wait (lead-cw7m): "
                        f"{prompt_name!r}\n"
                    )
                    # Continue the loop: re-wait for the input-ready marker.
                if not input_ready:
                    # BOUNDED: the scan-dismiss loop terminated at the 60s
                    # deadline rather than looping indefinitely.  Stop
                    # attempting dismissals, warn that the main input did not
                    # become ready, and proceed WITHOUT injecting.
                    reason = (
                        f"readiness failure: Claude Code workspace-trust prompt "
                        f"did not clear / main input did not become ready "
                        f"within {CLAUDE_READINESS_TIMEOUT_SECONDS:.0f} seconds "
                        f"(marker {CLAUDE_INPUT_READY_MARKER!r} not seen; the "
                        f"readiness barrier never reported both supporting "
                        f"servers ready); startup prompt NOT injected"
                    )
                    err_lines.append("warning: " + reason + "\n")
                    self._write_launch_diagnostic(
                        bc_name, CAUSE_MARKER_READINESS, reason, err_lines
                    )
                    return CommandResult(
                        exit_code=1,
                        stdout="".join(out_lines),
                        stderr="".join(err_lines),
                    )
            # Step 4b: blocking interactive option-screen handling (lead-q3uy).
            #
            # After the input-ready marker but BEFORE the prompt is submitted,
            # the agent runtime can present a blocking interactive option
            # screen that absorbs keystrokes.  Capture the pane ONCE and
            # classify it:
            #   * recognized blocking option screen WITH an escape affordance →
            #     send a DISCRETE send-keys carrying ONLY the Escape key (never
            #     Enter — and never an Enter to "select a default"), capture the
            #     dismissed screen's content, log it as a host-discoverable
            #     WARNING, then fall through to submit the prompt directly;
            #   * recognized blocking option screen with NO escape affordance →
            #     do NOT send Enter / do NOT auto-confirm a default; surface a
            #     WARNING naming the un-escapable screen and do NOT submit the
            #     prompt (which the screen would swallow);
            #   * no blocking option screen → proceed to submit as normal.
            pane = self._driver.capture_pane(container, AGENT_TMUX_SESSION)
            if OPTION_SCREEN_MARKER in pane:
                if ESCAPE_AFFORDANCE_MARKER in pane:
                    # Capture the rendered content BEFORE dismissing, so the
                    # WARNING records exactly what was auto-dismissed.
                    dismissed_content = pane
                    # Discrete send-keys carrying ONLY the Escape key payload —
                    # NOT Enter, and NOT a text+Enter pair.  This dismisses the
                    # escape-able screen without selecting any default option.
                    self._driver.exec_run(
                        container,
                        ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION,
                         ESCAPE_KEY_NAME],
                        user=AGENT_CONTAINER_USER,
                    )
                    err_lines.append(
                        "warning: an interactive option screen was "
                        "auto-dismissed during engage (sent Escape to the "
                        f"tmux session {AGENT_TMUX_SESSION!r}); rendered "
                        "content of the dismissed screen follows so a human "
                        "can review what was auto-dismissed (lead-q3uy):\n"
                        f"{dismissed_content}\n"
                    )
                    out_lines.append(
                        "Auto-dismissed a blocking interactive option screen "
                        "with Escape during engage (lead-q3uy)\n"
                    )
                else:
                    # No escape affordance: refuse to auto-confirm.  Pressing
                    # Enter here would blindly select whatever option is
                    # highlighted, so send NOTHING and do NOT submit the prompt.
                    err_lines.append(
                        "warning: engage encountered a blocking interactive "
                        "screen with NO escape/dismiss affordance; the launcher "
                        "did NOT send Enter and did NOT auto-confirm a default; "
                        "the startup prompt was NOT submitted.  Un-escapable "
                        "screen content follows so a human can review it from "
                        f"the host (lead-q3uy):\n{pane}\n"
                    )
                    return CommandResult(
                        exit_code=0,
                        stdout="".join(out_lines),
                        stderr="".join(err_lines),
                    )

            # Step 5: inject the startup prompt into Claude Code's input.
            #
            # Two DISCRETE send-keys invocations (text first, Enter second),
            # NOT one invocation carrying both (lead-lez1 / lead-9q0f root
            # cause).  A single `send-keys <text> Enter` exec_run concatenates
            # the whole keystream into ONE pty write() syscall; Claude Code's
            # TUI treats single-write payloads above ~70 bytes as a paste and
            # absorbs the trailing CR into the input buffer instead of
            # submitting.  Two exec_run calls are two discrete pty writes
            # separated by a kernel-scheduling gap, which the TUI processes as
            # a discrete submit keypress.
            self._driver.exec_run(
                container,
                ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, startup_prompt],
                user=AGENT_CONTAINER_USER,
            )
            self._driver.exec_run(
                container,
                ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, "Enter"],
                user=AGENT_CONTAINER_USER,
            )
            out_lines.append(f"Injected startup prompt: {startup_prompt!r}\n")

        return CommandResult(
            exit_code=0, stdout="".join(out_lines), stderr="".join(err_lines)
        )

    # ------------------------------------------------------------------
    # start-agent — recovery: drive agent-start against an already-cloned
    # healthy container without re-cloning (lead-k4k7)
    # ------------------------------------------------------------------

    def start_agent(
        self,
        bc_name: str,
        startup_prompt: str | None = None,
        shopmsg_dsn: str | None = None,
        agent_vault_broker: str | None = None,
        manifest_path: Path | None = None,
    ) -> CommandResult:
        """Recovery subcommand: drive the agent-start sequence against an
        ALREADY-cloned, healthy container that has no agent — WITHOUT
        re-cloning.

        lead-k4k7.  Makes first-class the manual recovery the lead performed
        when a transient skill-refresh failure stranded a fully-cloned
        "Up (healthy)" container with no agent session.  It runs the SAME
        agent-start sequence ``launch`` uses (``_start_agent_session``): tmux
        new-session as vscode, the messaging-DB + agent-vault readiness
        barriers, ``agent-vault run -- claude``, the readiness-marker waits,
        and the prompt injection — but NO clone, NO beads provisioning, and NO
        skill-refresh.  It is idempotent / safe to re-run on a container
        stranded with a clone but no agent.

        Resolution of the readiness-probe inputs mirrors ``launch``: the DSN
        comes from ``shopmsg_dsn`` (falling back to the container's recorded
        DSN, then the ``SHOPMSG_DSN`` process env), and the probe broker is
        derived from the resolved product slug (an explicit broker still wins).
        """
        container = _container_name(bc_name)

        if not self._driver.is_running(container):
            return CommandResult(
                exit_code=1,
                stderr=(
                    f"{container} is not running; start-agent recovers an "
                    "already-cloned, healthy container that has no agent — "
                    "run `bc-container launch` first to create it\n"
                ),
            )

        # lead-pixf (aeebb281): detect an ALREADY-live agent and NO-OP.
        #
        # start-agent's purpose is to RECOVER a container stranded with no
        # agent.  When the "agent" tmux session ALREADY holds a live claude
        # at the input-ready marker, there is nothing to recover: re-running
        # the agent-start sequence would (a) start a SECOND
        # `agent-vault run -- claude` in the same session and (b) block on
        # the readiness-marker probe until it times out, since a session
        # already past input-ready never re-presents the trust banner.  So
        # short-circuit BEFORE the agent-start sequence: report the agent is
        # already live and online, exit zero, and DO NOT touch the session
        # (no readiness probe, no second claude).
        if self._agent_online(container):
            return CommandResult(
                exit_code=0,
                stdout=(
                    f"{container} already has a live agent and is online; "
                    "start-agent is a no-op (no readiness probe run, no "
                    "second claude agent started)\n"
                ),
            )

        out_lines: list[str] = []
        err_lines: list[str] = []

        # Resolve the messaging DSN for the readiness barrier: explicit arg >
        # SHOPMSG_DSN process env.  start-agent recovers a container that was
        # launched with its DSN already baked into the container env, so the
        # readiness barrier is best driven from the SAME source the operator
        # used at launch (an explicit --shopmsg-dsn, or the SHOPMSG_DSN env).
        dsn = shopmsg_dsn or os.environ.get(SHOPMSG_DSN_ENV)

        # Resolve the probe broker address the same way launch does: an
        # explicit broker wins, else derive from the resolved product slug
        # (manifest product > SHOPMSG_SYSTEM_SLUG env > default).
        explicit_broker = (
            agent_vault_broker
            or os.environ.get("BCLAUNCHER_AGENT_VAULT_BROKER")
        )
        manifest_product: str | None = None
        try:
            manifest_product = _read_product_from_manifest(
                manifest_path or Path("bc-manifest.yaml")
            )
        except ManifestProductTypeError:
            manifest_product = None
        if env_system_slug := os.environ.get(SHOPMSG_SYSTEM_SLUG_ENV):
            resolved_system_slug = env_system_slug
        elif manifest_product:
            resolved_system_slug = manifest_product
        else:
            resolved_system_slug = DEFAULT_SYSTEM_SLUG
        probe_broker_address = resolve_probe_broker_address(
            explicit_broker, resolved_system_slug
        )

        out_lines.append(
            f"Recovering agent in already-cloned container {container} "
            "(no re-clone)\n"
        )
        return self._start_agent_session(
            bc_name,
            container,
            startup_prompt,
            dsn,
            probe_broker_address,
            out_lines,
            err_lines,
        )

    # ------------------------------------------------------------------
    # attach
    # ------------------------------------------------------------------

    def attach(self, bc_name: str) -> None:
        """
        Attach to the BC container's tmux session interactively.
        Replaces the current process via exec.
        """
        container = _container_name(bc_name)
        # Attach as vscode: the agent tmux session is owned by vscode (see
        # launch()), and tmux refuses cross-user attach.
        self._driver.exec_interactive(
            container,
            ["tmux", "attach-session", "-t", AGENT_TMUX_SESSION],
            user=AGENT_CONTAINER_USER,
        )

    # ------------------------------------------------------------------
    # inject
    # ------------------------------------------------------------------

    def inject(self, bc_name: str, prompt_text: str) -> CommandResult:
        """Send text to the container's tmux session."""
        container = _container_name(bc_name)
        # send-keys against the vscode-owned tmux server must run as vscode.
        #
        # Two DISCRETE send-keys invocations (text first, Enter second), NOT
        # one invocation carrying both (lead-lez1 / lead-9q0f root cause).  A
        # single `send-keys <text> Enter` exec_run concatenates the whole
        # keystream into ONE pty write() syscall; Claude Code's TUI treats
        # single-write payloads above ~70 bytes as a paste and absorbs the
        # trailing CR into the input buffer instead of submitting.  Two
        # exec_run calls are two discrete pty writes separated by a
        # kernel-scheduling gap, which the TUI processes as a discrete submit.
        self._driver.exec_run(
            container,
            ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, prompt_text],
            user=AGENT_CONTAINER_USER,
        )
        self._driver.exec_run(
            container,
            ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, "Enter"],
            user=AGENT_CONTAINER_USER,
        )
        return CommandResult(
            exit_code=0,
            stdout=f"Sent {prompt_text!r} to {AGENT_TMUX_SESSION} in {container}\n",
        )

    # ------------------------------------------------------------------
    # monitor
    # ------------------------------------------------------------------

    def monitor(self, bc_name: str) -> CommandResult:
        """Capture and return the current contents of the tmux session pane."""
        container = _container_name(bc_name)
        # capture-pane against the vscode-owned tmux server must run as vscode.
        result = self._driver.exec_run(
            container,
            ["tmux", "capture-pane", "-p", "-t", AGENT_TMUX_SESSION],
            user=AGENT_CONTAINER_USER,
        )
        return CommandResult(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    # ------------------------------------------------------------------
    # stop
    # ------------------------------------------------------------------

    def stop(self, bc_name: str) -> CommandResult:
        """Stop and remove the BC container."""
        container = _container_name(bc_name)
        self._driver.stop(container)
        return CommandResult(exit_code=0, stdout=f"Stopped {container}\n")

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def status(self, bc_name: str) -> CommandResult:
        """Report running state of the BC container and its tmux session.

        lead-wdvx (Bug 2): ``status`` is docker-dependent — its first act
        probes docker for the container's running state.  When that probe
        fails because the docker socket is unreachable (daemon down, OR a
        CONFIG fault: socket mounted-but-permission-denied, or not mounted),
        the driver raises ``DockerSocketUnreachableError``.  We surface that
        as a NON-ZERO exit with a stderr line NAMING the cause, distinct from
        the legitimate "container_state: stopped" an absent container reports
        at exit 0 — so a docker config fault is never masked as a (false)
        absent/stopped result.
        """
        container = _container_name(bc_name)
        try:
            is_running = self._driver.is_running(container)
        except DockerSocketUnreachableError as exc:
            detail = str(exc).strip()
            stderr = (
                "bc-container status: the Docker socket could not be reached "
                "(the Docker daemon is unreachable); cannot determine "
                "container state"
            )
            if detail:
                stderr += f": {detail}"
            return CommandResult(exit_code=1, stdout="", stderr=stderr + "\n")

        if not is_running:
            return CommandResult(
                exit_code=0,
                stdout=(
                    f"bc_name: {bc_name}\n"
                    f"container: {container}\n"
                    f"container_state: stopped\n"
                ),
            )

        # Check tmux session.  has-session against the vscode-owned tmux
        # server must run as vscode, same as every other tmux client call.
        tmux_result = self._driver.exec_run(
            container,
            ["tmux", "has-session", "-t", AGENT_TMUX_SESSION],
            user=AGENT_CONTAINER_USER,
        )
        tmux_state = "active" if tmux_result.returncode == 0 else "inactive"

        # Agent presence (lead-pixf / f2ddd6c7).  The tmux "agent" session
        # being merely present ("active") does NOT by itself mean an agent
        # is actually doing work: an empty session left at a bash prompt is
        # "active" but offline.  An agent is ONLINE only when the "agent"
        # tmux session holds a LIVE claude process whose `shop-msg watch`
        # inbox watcher is armed — that is the state in which the BC is
        # actually reachable for dispatched work.  Anything short of that
        # (no session, a session with no live claude, or a claude whose
        # watcher is not armed) is reported as "offline".
        agent_presence = (
            "online" if self._agent_online(container) else "offline"
        )

        return CommandResult(
            exit_code=0,
            stdout=(
                f"bc_name: {bc_name}\n"
                f"container: {container}\n"
                f"container_state: running\n"
                f"tmux_session: {tmux_state}\n"
                f"agent_presence: {agent_presence}\n"
            ),
        )

    # ------------------------------------------------------------------
    # agent presence (lead-pixf) — shared by status + start-agent
    # ------------------------------------------------------------------

    def _agent_online(self, container: str) -> bool:
        """Return True when the container's "agent" tmux session holds a LIVE
        claude process whose ``shop-msg watch`` inbox watcher is armed.

        lead-pixf.  This is the agent-presence determinant for the ``status``
        report (f2ddd6c7) and the no-op short-circuit for ``start-agent``
        (aeebb281).  It is delegated to the driver so the real driver can
        probe the live in-container process table / watcher state while the
        fake can model a live-agent container directly.  When the driver does
        not expose the probe (older driver), presence resolves to False so the
        command degrades to "offline" rather than crashing.
        """
        probe = getattr(self._driver, "agent_online", None)
        return bool(probe(container)) if callable(probe) else False

    # ------------------------------------------------------------------
    # readiness sequence (messaging-DB barrier, idempotent)
    # ------------------------------------------------------------------

    def ensure_ready(
        self,
        bc_name: str,
        shopmsg_dsn: str | None = None,
    ) -> CommandResult:
        """Run (or re-run) the messaging readiness sequence for the container.

        Idempotent: re-running against a container that has already passed
        its readiness sequence is a no-op that exits zero and reports the
        container is already ready — it does NOT re-send any startup prompt.

        Returns non-zero with a DSN-naming stderr line when the messaging
        database is unreachable.
        """
        container = _container_name(bc_name)
        dsn = shopmsg_dsn or os.environ.get(SHOPMSG_DSN_ENV)

        if self._container_marked_ready(container):
            return CommandResult(
                exit_code=0,
                stdout=f"{container} is already ready\n",
            )

        if dsn and not self._driver.messaging_db_reachable(dsn):
            return CommandResult(
                exit_code=1,
                stdout="",
                stderr=(
                    f"messaging readiness failure: messaging database at "
                    f"{SHOPMSG_DSN_ENV}={dsn} is not reachable\n"
                ),
            )

        self._mark_container_ready(container)
        return CommandResult(
            exit_code=0,
            stdout=f"{container} is ready\n",
        )

    # lead-q5k7 — derive the shop-type for `shop-templates update
    # --shop-type <bc|lead>` from the cloned shop's own canonical marker
    # file `.claude/shop/type.md` (contents "bc" or "lead").  The update
    # MUST use the type the shop was originally bootstrapped with, and the
    # cloned repo carries that marker, so this is the authoritative source.
    # Defaults to "bc" when the marker is absent/unreadable/unrecognised so
    # the refresh still runs with the dominant shop type rather than
    # crashing the launch.
    def _read_shop_type(self, container: str) -> str:
        result = self._driver.exec_run(
            container,
            ["cat", f"{CONTAINER_WORKSPACE}/.claude/shop/type.md"],
        )
        if result.returncode == 0:
            value = (result.stdout or "").strip().lower()
            if value in ("bc", "lead"):
                return value
        return "bc"

    # Readiness bookkeeping is delegated to the driver so the fake can model
    # an already-ready container.  Real drivers may persist this as a
    # container label / sentinel; the fake holds it in memory.
    def _container_marked_ready(self, container: str) -> bool:
        marker = getattr(self._driver, "is_marked_ready", None)
        return bool(marker(container)) if callable(marker) else False

    def _mark_container_ready(self, container: str) -> None:
        marker = getattr(self._driver, "mark_ready", None)
        if callable(marker):
            marker(container)

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------

    def health(self, bc_name: str) -> CommandResult:
        """Report the BC container's Docker health status.

        The container is healthy only when beads is functionally usable
        inside it AND the messaging database at SHOPMSG_DSN is reachable;
        otherwise it is unhealthy even if the agent process is alive.  This
        mirrors the in-container healthcheck the launch wires up; the host
        reads the resulting status via ``docker inspect``.
        """
        container = _container_name(bc_name)
        status = self._driver.health_status(container)
        return CommandResult(
            exit_code=0 if status == "healthy" else 1,
            stdout=f"{status}\n",
        )

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    def list_containers(self) -> CommandResult:
        """List all known BC containers with their states.

        lead-pixf (010e776c): when the Docker socket is unreachable, the
        driver raises ``DockerSocketUnreachableError`` rather than returning
        an empty list.  We surface that as a NON-ZERO exit with a stderr
        line naming the docker-socket unreachability and emit NOTHING on
        stdout — in particular NOT "No BC containers found", which would
        mask an infrastructure outage as a (false) empty inventory.
        """
        try:
            infos = self._driver.list_bc_containers()
        except DockerSocketUnreachableError as exc:
            detail = str(exc).strip()
            stderr = (
                "bc-container list: the Docker socket could not be reached "
                "(the Docker daemon is unreachable); cannot enumerate BC "
                "containers"
            )
            if detail:
                stderr += f": {detail}"
            return CommandResult(exit_code=1, stdout="", stderr=stderr + "\n")
        if not infos:
            return CommandResult(exit_code=0, stdout="No BC containers found.\n")

        lines: list[str] = []
        for info in infos:
            # Derive bc_name by stripping leading "bc-"
            bc_name = info.name.removeprefix("bc-")
            state = "running" if info.running else "stopped"
            lines.append(f"{bc_name}: {state}\n")

        return CommandResult(exit_code=0, stdout="".join(lines))

    # ------------------------------------------------------------------
    # isolation check (used by tests and the isolate-check subcommand)
    # ------------------------------------------------------------------

    def get_bind_mounts(self, container_name: str) -> list[ContainerMount]:
        """Return only bind-type mounts for a running container."""
        all_mounts = self._driver.get_mounts(container_name)
        return [m for m in all_mounts if m.type == "bind"]

    def last_command(self) -> list[str]:
        return self._driver.last_command()
